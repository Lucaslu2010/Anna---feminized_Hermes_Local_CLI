from PySide6.QtCore import Qt
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

from file_text_extractor import extract_text_from_bytes
from rag_client import EmbeddingClient
from rag_context import RagContextManager
from rag_files import clear_rag_storage_files, copy_file_to_rag_storage, delete_rag_storage_file
from rag_ingest import clean_text_for_rag, is_probably_garbled_text, summarize_text
from rag_settings_dialog import RagSettingsDialog
from rag_store import RagStore


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


class RagManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("RAG Memory")
        self.setModal(False)
        self.resize(860, 560)
        self.setStyleSheet(STYLE)

        self.store = RagStore()
        self.manager = RagContextManager(client=EmbeddingClient(), store=self.store)

        tabs = QTabWidget()
        tabs.addTab(self.build_memory_tab(), "Browse")
        tabs.addTab(self.build_settings_tab(), "Settings")

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        self.setLayout(layout)

        self.refresh_sources()

    def build_memory_tab(self):
        widget = QWidget()

        self.sources_list = QListWidget()
        self.sources_list.currentTextChanged.connect(self.show_source)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sources_list)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        add_button = QPushButton("Add File")
        add_button.clicked.connect(self.add_file)

        delete_button = QPushButton("Delete Selected")
        delete_button.clicked.connect(self.delete_selected)

        clear_button = QPushButton("Clear All")
        clear_button.clicked.connect(self.clear_all)

        clean_button = QPushButton("Clean Garbled")
        clean_button.clicked.connect(self.clean_garbled)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_sources)

        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(delete_button)
        button_row.addWidget(clear_button)
        button_row.addWidget(clean_button)
        button_row.addStretch(1)
        button_row.addWidget(refresh_button)

        self.status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.status_label)
        widget.setLayout(layout)
        return widget

    def build_settings_tab(self):
        widget = QWidget()

        open_settings = QPushButton("Open Embedding Settings")
        open_settings.clicked.connect(self.open_embedding_settings)

        label = QLabel(
            "Embedding settings control the provider, API key, base URL, model, and optional dimensions used when indexing files."
        )
        label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(label)
        layout.addWidget(open_settings)
        layout.addStretch(1)
        widget.setLayout(layout)
        return widget

    def refresh_sources(self):
        self.sources_list.clear()
        sources = self.store.list_sources()

        for item in sources:
            label = f"{item['source']}  ({item['chunk_count']} chunks)"
            self.sources_list.addItem(label)

        self.status_label.setText(f"{len(sources)} source(s), {self.store.count_chunks()} chunk(s)")
        if not sources:
            self.preview.setPlainText("No RAG memory indexed yet.")

    def selected_source(self):
        item = self.sources_list.currentItem()
        if not item:
            return ""

        return item.text().rsplit("  (", 1)[0]

    def show_source(self):
        source = self.selected_source()
        if not source:
            return

        chunks = self.store.get_source_chunks(source)
        parts = []
        for chunk in chunks:
            text = clean_text_for_rag(chunk["text"])
            if not text or is_probably_garbled_text(chunk["text"]):
                text = "[Filtered garbled chunk]"
            summary = chunk.get("summary") or summarize_text(text)
            parts.append(
                f"# Chunk {chunk['chunk_index']}\n\n"
                f"Summary:\n{summary}\n\n"
                f"Text:\n{text}"
            )

        self.preview.setPlainText("\n\n---\n\n".join(parts))

    def add_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add files to RAG",
            "",
            "Documents (*.txt *.md *.py *.js *.ts *.json *.csv *.log *.yaml *.yml *.docx);;All Files (*)",
        )
        if not paths:
            return

        for path in paths:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                _, text = extract_text_from_bytes(path, data)
                source = copy_file_to_rag_storage(path)
                status = self.manager.add_file_text(source, text)
                self.status_label.setText(status)
            except Exception as e:
                QMessageBox.warning(self, "Could Not Add File", f"{path}\n\n{e}")

        self.refresh_sources()

    def delete_selected(self):
        source = self.selected_source()
        if not source:
            return

        self.store.delete_source(source)
        delete_rag_storage_file(source)
        self.preview.clear()
        self.refresh_sources()

    def clear_all(self):
        result = QMessageBox.question(
            self,
            "Clear RAG Memory",
            "Delete all indexed RAG memory?",
        )
        if result != QMessageBox.Yes:
            return

        self.store.delete_all()
        clear_rag_storage_files()
        self.preview.clear()
        self.refresh_sources()

    def clean_garbled(self):
        deleted = self.store.delete_garbled_chunks()
        self.preview.clear()
        self.refresh_sources()
        QMessageBox.information(
            self,
            "Clean Complete",
            f"Removed {deleted} garbled RAG chunk(s).",
        )

    def open_embedding_settings(self):
        dialog = RagSettingsDialog(self)
        if dialog.exec():
            self.manager.reload_config()
