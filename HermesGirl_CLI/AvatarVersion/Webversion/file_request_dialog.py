from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class MissingFileDialog(QDialog):
    CHOOSE_FILE = "choose"
    FORGET_FILE = "forget"

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)

        self.choice = ""
        self.selected_path = ""
        self.filename = filename or "this file"

        self.setWindowTitle("File Needed")
        self.setModal(True)
        self.resize(460, 160)
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }

            QLabel {
                color: #24292f;
                font-size: 13px;
            }

            QPushButton {
                border: 1px solid #d0d7de;
                border-radius: 8px;
                padding: 8px 12px;
                background-color: #f6f8fa;
                color: #24292f;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #eef2f6;
            }
        """)

        label = QLabel(
            "Hermes wants to use this file again, but the local path is missing:\n\n"
            f"{self.filename}"
        )
        label.setWordWrap(True)

        choose_button = QPushButton("Choose File")
        choose_button.clicked.connect(self.choose_file)

        forget_button = QPushButton("Tell Agent Not To Use This File")
        forget_button.clicked.connect(self.forget_file)

        buttons = QHBoxLayout()
        buttons.addWidget(choose_button)
        buttons.addWidget(forget_button)

        layout = QVBoxLayout()
        layout.addWidget(label)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Choose {self.filename}",
            "",
            "All Files (*)",
        )
        if not path:
            return
        self.choice = self.CHOOSE_FILE
        self.selected_path = path
        self.accept()

    def forget_file(self):
        self.choice = self.FORGET_FILE
        self.accept()
