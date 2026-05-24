from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from web_agent_client import WebAgentClient
from web_settings import load_web_config, normalize_server_url, save_web_config


LOGIN_STYLE = """
    QDialog {
        background-color: #ffffff;
    }

    QLabel {
        color: #24292f;
    }

    QLabel#loginTitle {
        font-size: 18px;
        font-weight: 700;
        padding-bottom: 2px;
    }

    QLabel#loginSubtitle,
    QLabel#loginStatus {
        color: #57606a;
        font-size: 12px;
    }

    QLineEdit {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
        color: #24292f;
        selection-background-color: #0969da;
        padding: 7px 9px;
    }

    QLineEdit:focus {
        border-color: #0969da;
    }

    QPushButton {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        padding: 7px 13px;
        background-color: #f6f8fa;
        color: #24292f;
    }

    QPushButton:hover {
        background-color: #eef2f6;
    }
"""


class WebLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anna Server Login")
        self.setModal(True)
        self.resize(500, 300)
        self.setStyleSheet(LOGIN_STYLE)

        config = load_web_config()

        self.server_input = QLineEdit()
        self.server_input.setText(config.get("server_url", ""))
        self.server_input.setPlaceholderText("http://server-ip:8765")

        self.username_input = QLineEdit()
        self.username_input.setText(config.get("username", ""))
        self.username_input.setPlaceholderText("Username")

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password")
        self.password_input.returnPressed.connect(self.login)

        self.status_label = QLabel("")
        self.status_label.setObjectName("loginStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        form = QFormLayout()
        form.addRow("Server", self.server_input)
        form.addRow("Account", self.username_input)
        form.addRow("Password", self.password_input)

        login_button = QPushButton("Login")
        login_button.clicked.connect(self.login)
        self.login_button = login_button

        register_button = QPushButton("Register")
        register_button.clicked.connect(self.register)
        self.register_button = register_button

        buttons = QDialogButtonBox()
        buttons.addButton(login_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(register_button, QDialogButtonBox.ActionRole)
        cancel_button = buttons.addButton(QDialogButtonBox.Cancel)
        cancel_button.clicked.connect(self.reject)

        title = QLabel("Remote Anna Account")
        title.setObjectName("loginTitle")
        subtitle = QLabel("Use the same server account for chat, files, memory, and RAG.")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def client(self) -> WebAgentClient:
        return WebAgentClient(server_url=self.server_url())

    def server_url(self) -> str:
        return normalize_server_url(self.server_input.text().strip())

    def credentials(self):
        return self.username_input.text().strip(), self.password_input.text()

    def set_busy(self, busy: bool):
        self.login_button.setEnabled(not busy)
        self.register_button.setEnabled(not busy)

    def login(self):
        username, password = self.credentials()
        if not username or not password:
            self.status_label.setText("Enter an account and password.")
            return

        self.set_busy(True)
        self.status_label.setText("Logging in...")
        try:
            result = self.client().login(username, password)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        finally:
            self.set_busy(False)

        if result.get("ok") and result.get("token"):
            self.status_label.setText("Login successful.")
            self.accept()
            return

        self.status_label.setText(result.get("error") or "Login failed.")

    def register(self):
        username, password = self.credentials()
        if not username or not password:
            self.status_label.setText("Enter an account and password.")
            return

        self.set_busy(True)
        self.status_label.setText("Sending registration request...")
        try:
            save_web_config({"server_url": self.server_url()})
            result = self.client().register(username, password)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        finally:
            self.set_busy(False)

        if result.get("ok"):
            self.status_label.setText("Registration sent. An admin must approve this account before login.")
            return

        self.status_label.setText(result.get("error") or "Registration failed.")


def ensure_web_login(parent=None) -> bool:
    config = load_web_config()
    if not config.get("web_mode_enabled"):
        return True

    token = config.get("auth_token", "")
    if token:
        status = WebAgentClient().me()
        if status.get("ok"):
            return True

    dialog = WebLoginDialog(parent)
    return dialog.exec() == QDialog.Accepted
