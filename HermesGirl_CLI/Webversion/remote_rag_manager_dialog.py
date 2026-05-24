import threading
import os

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cloud_loading_overlay import CloudLoadingOverlay
from web_agent_client import WebAgentClient


STYLE = """
    QDialog {
        background-color: #ffffff;
    }

    QLabel {
        color: #24292f;
    }

    QListWidget,
    QTextEdit {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        padding: 7px;
        background-color: #ffffff;
        color: #24292f;
        selection-background-color: #ddf4ff;
        selection-color: #24292f;
    }

    QPushButton {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        padding: 7px 13px;
        background-color: #f6f8fa;
        color: #24292f;
        font-weight: 600;
    }

    QPushButton:hover {
        background-color: #eef2f6;
    }
"""


class RemoteFileManagerDialog(QDialog):
    files_loaded = Signal(list)
    rag_sources_loaded = Signal(list)
    rag_source_loaded = Signal(dict)
    reindex_finished = Signal(dict)
    load_failed = Signal(str)
    rag_load_failed = Signal(str)
    reindex_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.client = WebAgentClient()
        self.files = []
        self.rag_sources = []
        self.setWindowTitle("Server Files")
        self.setModal(False)
        self.resize(900, 560)
        self.setStyleSheet(STYLE)

        tabs = QTabWidget()
        tabs.addTab(self.build_files_tab(), "Files")
        tabs.addTab(self.build_rag_tab(), "Original RAG")

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        self.setLayout(layout)

        self.loading_overlay = CloudLoadingOverlay(self)

        self.files_loaded.connect(self.handle_files_loaded)
        self.rag_sources_loaded.connect(self.handle_rag_sources_loaded)
        self.rag_source_loaded.connect(self.handle_rag_source_loaded)
        self.reindex_finished.connect(self.handle_reindex_finished)
        self.load_failed.connect(self.handle_load_failed)
        self.rag_load_failed.connect(self.handle_rag_load_failed)
        self.reindex_failed.connect(self.handle_reindex_failed)
        self.refresh_files()
        self.refresh_rag_sources()

    def build_files_tab(self):
        widget = QWidget()

        self.files_list = QListWidget()
        self.files_list.currentRowChanged.connect(self.show_selected_file)

        self.file_preview = QTextEdit()
        self.file_preview.setReadOnly(True)

        add_button = QPushButton("Upload Files")
        add_button.clicked.connect(self.add_file)

        reindex_button = QPushButton("Reindex Selected")
        reindex_button.clicked.connect(self.reindex_selected)

        forget_button = QPushButton("Delete Selected")
        forget_button.clicked.connect(self.forget_selected)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_files)

        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(reindex_button)
        button_row.addWidget(forget_button)
        button_row.addStretch(1)
        button_row.addWidget(refresh_button)

        self.file_status_label = QLabel("")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.files_list)
        splitter.addWidget(self.file_preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.file_status_label)
        widget.setLayout(layout)
        return widget

    def build_rag_tab(self):
        widget = QWidget()

        self.rag_sources_list = QListWidget()
        self.rag_sources_list.currentRowChanged.connect(self.show_selected_rag_source)

        self.rag_preview = QTextEdit()
        self.rag_preview.setReadOnly(True)

        refresh_button = QPushButton("Refresh RAG")
        refresh_button.clicked.connect(self.refresh_rag_sources)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(refresh_button)

        self.rag_status_label = QLabel("")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.rag_sources_list)
        splitter.addWidget(self.rag_preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.rag_status_label)
        widget.setLayout(layout)
        return widget

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def refresh_files(self):
        self.file_status_label.setText("Loading server files...")
        self.loading_overlay.start()
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            self.files_loaded.emit(self.client.list_files())
        except Exception as e:
            self.load_failed.emit(str(e))

    def refresh_rag_sources(self):
        self.rag_status_label.setText("Loading server RAG...")
        self.loading_overlay.start()
        threading.Thread(target=self._refresh_rag_worker, daemon=True).start()

    def _refresh_rag_worker(self):
        try:
            self.rag_sources_loaded.emit(self.client.list_rag_sources())
        except Exception as e:
            self.rag_load_failed.emit(str(e))

    def handle_files_loaded(self, files):
        self.loading_overlay.stop()
        self.files = [
            item
            for item in list(files or [])
            if item.get("status") != "forgotten"
        ]
        self.files_list.clear()
        for item in self.files:
            status = item.get("status", "active")
            filename = item.get("filename") or item.get("key", "")
            self.files_list.addItem(f"{filename}  [{status}]")
        self.file_status_label.setText(f"{len(self.files)} uploaded file(s)")
        if not self.files:
            self.file_preview.setPlainText("No server-side files have been uploaded yet.")

    def handle_rag_sources_loaded(self, sources):
        self.loading_overlay.stop()
        self.rag_sources = list(sources or [])
        self.rag_sources_list.clear()
        for item in self.rag_sources:
            source = item.get("source", "")
            filename = os.path.basename(source) or source
            chunks = item.get("chunk_count", 0)
            self.rag_sources_list.addItem(f"{filename}  ({chunks} chunks)")
        self.rag_status_label.setText(f"{len(self.rag_sources)} RAG source(s)")
        if not self.rag_sources:
            self.rag_preview.setPlainText("No server-side RAG chunks have been indexed yet.")

    def handle_load_failed(self, message: str):
        self.loading_overlay.stop()
        self.file_status_label.setText(f"Could not load server files: {message}")

    def handle_rag_load_failed(self, message: str):
        self.loading_overlay.stop()
        self.rag_status_label.setText(f"Could not load server RAG: {message}")

    def show_selected_file(self, row: int):
        if row < 0 or row >= len(self.files):
            return

        item = self.files[row]
        self.file_preview.setPlainText(
            f"Key: {item.get('key', '')}\n"
            f"Filename: {item.get('filename', '')}\n"
            f"Status: {item.get('status', '')}\n"
            f"Original RAG indexed: {'yes' if item.get('rag_indexed') else 'no'}\n"
            f"Size: {format_bytes(item.get('size', 0))}\n"
            f"Server path: {item.get('server_path', '')}\n\n"
            f"Index status:\n{item.get('index_status', '')}\n\n"
            f"Summary:\n{item.get('summary', '')}"
        )

    def show_selected_rag_source(self, row: int):
        if row < 0 or row >= len(self.rag_sources):
            return

        source = self.rag_sources[row].get("source", "")
        if not source:
            return

        self.rag_status_label.setText("Loading RAG chunks...")
        self.loading_overlay.start()
        threading.Thread(
            target=self._load_rag_source_worker,
            args=(source,),
            daemon=True,
        ).start()

    def _load_rag_source_worker(self, source: str):
        try:
            self.rag_source_loaded.emit(self.client.get_rag_source(source))
        except Exception as e:
            self.rag_load_failed.emit(str(e))

    def handle_rag_source_loaded(self, payload):
        self.loading_overlay.stop()
        source = payload.get("source", "")
        chunks = payload.get("chunks", [])
        parts = []
        for chunk in chunks:
            parts.append(
                f"# Chunk {chunk.get('chunk_index', 0)}\n\n"
                f"Summary:\n{chunk.get('summary', '')}\n\n"
                f"Text:\n{chunk.get('text', '')}"
            )

        self.rag_preview.setPlainText("\n\n---\n\n".join(parts) or "No chunks found.")
        self.rag_status_label.setText(
            f"{os.path.basename(source) or source}: {len(chunks)} chunk(s)"
        )

    def add_file(self):
        parent = self.parent()
        chat_panel = getattr(parent, "chat_panel", None)
        if chat_panel is None:
            QMessageBox.warning(self, "Upload Unavailable", "The chat panel is not available.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Upload files",
            "",
            "Documents (*.txt *.md *.py *.js *.ts *.json *.csv *.log *.yaml *.yml *.docx);;All Files (*)",
        )
        if not paths:
            return

        for path in paths:
            chat_panel.upload_file_to_server(path)
        self.file_status_label.setText("Upload started. Refresh after upload finishes.")

    def forget_selected(self):
        row = self.files_list.currentRow()
        if row < 0 or row >= len(self.files):
            return

        key = self.files[row].get("key", "")
        if not key:
            return

        filename = self.files[row].get("filename", "this file")
        result = QMessageBox.question(
            self,
            "Delete Uploaded File",
            f"Delete {filename} from the server and remove its RAG chunks?",
        )
        if result != QMessageBox.Yes:
            return

        try:
            self.loading_overlay.start()
            self.client.forget_file(key)
            self.refresh_files()
            self.refresh_rag_sources()
        except Exception as e:
            self.loading_overlay.stop()
            QMessageBox.warning(self, "Could Not Delete File", str(e))

    def reindex_selected(self):
        row = self.files_list.currentRow()
        if row < 0 or row >= len(self.files):
            QMessageBox.information(self, "Reindex File", "Select an uploaded file first.")
            return

        key = self.files[row].get("key", "")
        if not key:
            return

        self.file_status_label.setText("Reindexing selected file...")
        self.loading_overlay.start()
        threading.Thread(
            target=self._reindex_worker,
            args=(key,),
            daemon=True,
        ).start()

    def _reindex_worker(self, key: str):
        try:
            self.reindex_finished.emit(self.client.reindex_files(key))
        except Exception as e:
            self.reindex_failed.emit(str(e))

    def handle_reindex_finished(self, result):
        self.loading_overlay.stop()
        indexed = result.get("indexed", 0)
        total = result.get("total", 0)
        detail = ""
        results = result.get("results", [])
        if results:
            detail = results[0].get("index_status", "")
        self.file_status_label.setText(
            f"Reindex complete: {indexed}/{total} indexed. {detail}".strip()
        )
        self.refresh_files()
        self.refresh_rag_sources()

    def handle_reindex_failed(self, message: str):
        self.loading_overlay.stop()
        QMessageBox.warning(self, "Could Not Reindex File", message)


def format_bytes(value) -> str:
    try:
        size = float(value or 0)
    except Exception:
        size = 0.0
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024


RemoteRagManagerDialog = RemoteFileManagerDialog
