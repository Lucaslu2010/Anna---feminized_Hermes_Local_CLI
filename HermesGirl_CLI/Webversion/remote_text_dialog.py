import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout

from cloud_loading_overlay import CloudLoadingOverlay
from skills_grid import SKILLS_GRID_STYLE, SkillsGridWidget


class RemoteTextDialog(QDialog):
    text_loaded = Signal(str)
    load_failed = Signal(str)

    def __init__(self, title: str, loader, parent=None):
        super().__init__(parent)

        self.loader = loader
        self.is_skills_dialog = "skills" in title.lower()
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(760, 560)
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }

            QLabel {
                color: #57606a;
                font-size: 12px;
            }

            QTextEdit {
                border: 1px solid #d0d7de;
                border-radius: 8px;
                padding: 8px;
                background-color: #ffffff;
                color: #24292f;
            }

            QPushButton {
                border: 1px solid #d0d7de;
                border-radius: 8px;
                padding: 7px 13px;
                background-color: #f6f8fa;
                color: #24292f;
                font-weight: 600;
            }
        """ + SKILLS_GRID_STYLE)

        if self.is_skills_dialog:
            self.text = None
            self.skills_grid = SkillsGridWidget()
        else:
            self.text = QTextEdit()
            self.text.setReadOnly(True)
            self.skills_grid = None

        self.status = QLabel("")

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh)
        if self.is_skills_dialog:
            layout.addWidget(self.skills_grid, stretch=1)
        else:
            layout.addWidget(self.text, stretch=1)
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.loading_overlay = CloudLoadingOverlay(self)

        self.text_loaded.connect(self.handle_text_loaded)
        self.load_failed.connect(self.handle_load_failed)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def refresh(self):
        self.status.setText("Loading from server...")
        self.loading_overlay.start()
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        try:
            self.text_loaded.emit(str(self.loader() or ""))
        except Exception as e:
            self.load_failed.emit(str(e))

    def handle_text_loaded(self, text: str):
        self.loading_overlay.stop()
        if self.is_skills_dialog:
            self.skills_grid.set_skills_from_text(text or "")
        else:
            self.text.setPlainText(text or "No server-side content found.")
        self.status.setText("Loaded from server.")

    def handle_load_failed(self, message: str):
        self.loading_overlay.stop()
        self.status.setText(f"Could not load from server: {message}")
