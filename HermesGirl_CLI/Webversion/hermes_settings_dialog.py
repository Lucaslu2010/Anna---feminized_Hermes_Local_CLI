import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from cloud_loading_overlay import CloudLoadingOverlay
from export_memory import (
    clear_hermes_memory_with_backup,
    delete_all_memory_backups,
    export_memory_archive,
    get_downloads_dir,
    import_memory_archive,
    list_memory_backups,
    restore_memory_backup,
)
from hermes_locator import describe_hermes_paths
from hermes_settings import (
    load_hermes_config,
    read_hermes_memory,
    read_hermes_skills_text,
    save_hermes_config,
)
from remote_memory_recovery_dialog import RemoteMemoryRecoveryDialog
from skills_grid import SKILLS_GRID_STYLE, SkillsGridWidget
from web_agent_client import WebAgentClient
from web_settings import load_web_config, save_web_config
from web_login_dialog import WebLoginDialog

PROVIDERS = [
    ("nous", "Nous Portal (Nous Research subscription)"),
    ("openrouter", "OpenRouter (100+ models, pay-per-use)"),
    ("lmstudio", "LM Studio (local desktop app with built-in model server)"),
    ("anthropic", "Anthropic (Claude models - API key or Claude Code)"),
    ("openai-codex", "OpenAI Codex"),
    ("xiaomi", "Xiaomi MiMo (MiMo-V2.5 and V2 models - pro, omni, flash)"),
    ("tencent", "Tencent TokenHub (Hy3 Preview - direct API)"),
    ("nvidia", "NVIDIA NIM (Nemotron models or local NIM)"),
    ("qwen-oauth", "Qwen OAuth (reuses local Qwen CLI login)"),
    ("github-copilot", "GitHub Copilot"),
    ("github-copilot-acp", "GitHub Copilot ACP"),
    ("huggingface", "Hugging Face Inference Providers"),
    ("google", "Google AI Studio"),
    ("gemini-oauth", "Google Gemini via OAuth + Code Assist"),
    ("deepseek", "DeepSeek"),
    ("xai", "xAI / Grok"),
    ("zai", "Z.AI / GLM"),
    ("kimi", "Kimi Coding Plan & Moonshot API"),
    ("kimi-cn", "Kimi / Moonshot China"),
    ("stepfun", "StepFun Step Plan"),
    ("minimax", "MiniMax"),
    ("minimax-oauth", "MiniMax via OAuth browser login"),
    ("minimax-cn", "MiniMax China"),
    ("dashscope", "Alibaba Cloud / DashScope Coding"),
    ("ollama", "Ollama Cloud"),
    ("arcee", "Arcee AI"),
    ("gmi", "GMI Cloud"),
    ("kilocode", "Kilo Code"),
    ("opencode-zen", "OpenCode Zen"),
    ("opencode-go", "OpenCode Go"),
    ("bedrock", "AWS Bedrock"),
    ("azure", "Azure Foundry"),
    ("vercel", "Vercel AI Gateway"),
    ("alibaba-coding", "Alibaba Cloud Coding Plan"),
    ("custom", "custom"),
    ("siliconflow", "silicon flow (api.siliconfloe.com/v1)"),
    ("siliconflow-cn", "Api.siliconflow.cn"),
]


DIALOG_STYLE = """
    QDialog {
        background-color: #ffffff;
    }

    QLabel {
        color: #24292f;
    }

    QLineEdit,
    QComboBox,
    QTextEdit {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
        color: #24292f;
        selection-background-color: #0969da;
    }

    QLineEdit {
        padding: 7px 9px;
    }

    QComboBox {
        padding: 7px 34px 7px 9px;
        min-height: 22px;
    }

    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 32px;
        border: none;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
        background-color: transparent;
    }

    QComboBox::down-arrow {
        image: none;
        width: 0px;
        height: 0px;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #57606a;
        margin-right: 10px;
    }

    QComboBox QAbstractItemView {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        padding: 4px;
        background-color: #ffffff;
        selection-background-color: #ddf4ff;
        selection-color: #24292f;
        outline: none;
    }

    QLineEdit:focus,
    QComboBox:focus,
    QTextEdit:focus {
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

    QLabel#memoryOperationStatus {
        color: #57606a;
        font-size: 12px;
        padding: 0px 2px;
    }
"""


def format_backup_time(created_at: str, mtime: float) -> str:
    for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(created_at), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(created_at or "")


class MemoryRecoveryDialog(QDialog):
    recovery_operation_finished = Signal(str, str)
    recovery_operation_failed = Signal(str)
    memory_restored = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Memory Recovery")
        self.setModal(False)
        self.resize(720, 420)
        self.setStyleSheet(DIALOG_STYLE)

        self.backup_list = QListWidget()
        self.backup_list.setAlternatingRowColors(True)

        self.status_label = QLabel("")
        self.status_label.setObjectName("memoryOperationStatus")

        self.restore_button = QPushButton("Restore Selected")
        self.restore_button.clicked.connect(self.restore_selected_backup)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_backups)

        self.delete_all_button = QPushButton("Clear All Backups")
        self.delete_all_button.clicked.connect(self.confirm_delete_all_backups)

        button_row = QHBoxLayout()
        button_row.addWidget(self.restore_button)
        button_row.addWidget(self.refresh_button)
        button_row.addStretch(1)
        button_row.addWidget(self.delete_all_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Available Anna memory backups"))
        layout.addWidget(self.backup_list)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.recovery_operation_finished.connect(self.handle_recovery_finished)
        self.recovery_operation_failed.connect(self.handle_recovery_failed)
        self.refresh_backups()

    def refresh_backups(self):
        self.backup_list.clear()
        backups = list_memory_backups()
        for backup in backups:
            time_text = format_backup_time(
                str(backup.get("created_at", "")),
                float(backup.get("mtime", 0)),
            )
            item = QListWidgetItem(f"{backup['name']}\nBackup time: {time_text}")
            item.setData(Qt.UserRole, backup["path"])
            item.setToolTip(str(backup["path"]))
            self.backup_list.addItem(item)

        has_backups = bool(backups)
        self.restore_button.setEnabled(has_backups)
        self.delete_all_button.setEnabled(has_backups)
        self.status_label.setText("" if has_backups else "No memory backups found.")

    def restore_selected_backup(self):
        item = self.backup_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Restore Memory", "Select a backup first.")
            return

        path = item.data(Qt.UserRole)
        self.set_operation_buttons_enabled(False)
        self.status_label.setText("Restoring memory...")
        worker = threading.Thread(target=self._restore_worker, args=(path,), daemon=True)
        worker.start()

    def _restore_worker(self, path: str):
        try:
            backup_path, imported_memory_path = restore_memory_backup(path)
            message = (
                "Memory recovery completed.\n\n"
                f"Current memory backup:\n{backup_path}\n\n"
                f"Restored memory document:\n{imported_memory_path or '(none found)'}"
            )
            self.recovery_operation_finished.emit("Memory Restored", message)
        except Exception as e:
            self.recovery_operation_failed.emit(str(e))

    def confirm_delete_all_backups(self):
        result = QMessageBox.question(
            self,
            "Clear All Backups",
            "Delete all Anna memory backups? This cannot be undone.",
        )
        if result != QMessageBox.Yes:
            return

        deleted = delete_all_memory_backups()
        self.refresh_backups()
        QMessageBox.information(
            self,
            "Backups Cleared",
            f"Deleted {deleted} memory backup{'s' if deleted != 1 else ''}.",
        )

    def set_operation_buttons_enabled(self, enabled: bool):
        self.restore_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.delete_all_button.setEnabled(enabled)

    def handle_recovery_finished(self, title: str, message: str):
        self.set_operation_buttons_enabled(True)
        self.status_label.setText("")
        self.memory_restored.emit()
        self.refresh_backups()
        QMessageBox.information(self, title, message)

    def handle_recovery_failed(self, message: str):
        self.set_operation_buttons_enabled(True)
        self.status_label.setText("")
        QMessageBox.warning(self, "Memory Recovery Failed", message)


class HermesSettingsDialog(QDialog):
    memory_operation_finished = Signal(str, str)
    memory_operation_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Hermes Settings")
        self.setModal(True)
        self.resize(720, 410)
        self.setStyleSheet(DIALOG_STYLE)

        config = load_hermes_config()
        web_config = load_web_config()
        paths = describe_hermes_paths(create=True)

        self.provider_input = QComboBox()
        self.provider_input.setEditable(False)
        self.populate_providers(config.get("provider", ""))

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(config.get("api_key", ""))

        self.base_url_input = QLineEdit()
        self.base_url_input.setText(config.get("base_url", ""))
        self.base_url_input.setPlaceholderText("https://api.example.com/v1")

        self.temperature_input = QLineEdit()
        self.temperature_input.setText(config.get("temperature", ""))
        self.temperature_input.setPlaceholderText("Example: 0.7")

        self.web_mode_input = QCheckBox("Use a remote Hermes server")
        self.web_mode_input.setChecked(bool(web_config.get("web_mode_enabled")))

        self.web_server_input = QLineEdit()
        self.web_server_input.setText(web_config.get("server_url", ""))
        self.web_server_input.setPlaceholderText("http://server-ip:8765")

        self.web_account_label = QLabel("")
        self.web_account_label.setObjectName("memoryOperationStatus")

        self.web_login_button = QPushButton("Login / Register")
        self.web_login_button.clicked.connect(self.open_web_login)

        self.web_logout_button = QPushButton("Logout")
        self.web_logout_button.clicked.connect(self.logout_web_account)

        account_row = QHBoxLayout()
        account_row.addWidget(self.web_account_label, stretch=1)
        account_row.addWidget(self.web_login_button)
        account_row.addWidget(self.web_logout_button)

        self.config_path_label = QLabel(
            f"Hermes: {paths['executable']}\n"
            f"Config: {paths['config']}\n"
            f"Secrets: {paths['env']}"
        )
        self.config_path_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Provider", self.provider_input)
        form.addRow("API Key", self.api_key_input)
        form.addRow("API URL", self.base_url_input)
        form.addRow("Temperature", self.temperature_input)
        form.addRow("Web Mode", self.web_mode_input)
        form.addRow("Server IP / URL", self.web_server_input)
        form.addRow("Account", account_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save)
        buttons.accepted.connect(self.save_config)

        export_memory_button = QPushButton("Export Memory")
        export_memory_button.clicked.connect(self.export_memory)
        self.export_memory_button = export_memory_button

        import_memory_button = QPushButton("Import Memory")
        import_memory_button.clicked.connect(self.import_memory)
        self.import_memory_button = import_memory_button

        recovery_button = QPushButton("Recovery")
        recovery_button.clicked.connect(self.open_memory_recovery)
        self.recovery_button = recovery_button

        memory_buttons = QHBoxLayout()
        memory_buttons.addWidget(export_memory_button)
        memory_buttons.addWidget(import_memory_button)
        memory_buttons.addWidget(recovery_button)
        memory_buttons.addStretch(1)

        self.memory_status_label = QLabel("")
        self.memory_status_label.setObjectName("memoryOperationStatus")

        self.memory_operation_finished.connect(self.handle_memory_operation_finished)
        self.memory_operation_failed.connect(self.handle_memory_operation_failed)

        layout = QVBoxLayout()
        layout.addWidget(self.config_path_label)
        layout.addLayout(form)
        layout.addLayout(memory_buttons)
        layout.addWidget(self.memory_status_label)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.loading_overlay = CloudLoadingOverlay(self)
        self.refresh_web_account_row()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def populate_providers(self, current_provider: str):
        current_provider = (current_provider or "").strip()

        known_values = {value for value, _ in PROVIDERS}
        if current_provider and current_provider not in known_values:
            self.provider_input.addItem(f"{current_provider} (current config)", current_provider)

        for value, label in PROVIDERS:
            self.provider_input.addItem(label, value)

        index = self.provider_input.findData(current_provider)
        if index >= 0:
            self.provider_input.setCurrentIndex(index)

    def save_config(self):
        temperature = self.temperature_input.text().strip()
        if temperature:
            try:
                float(temperature)
            except ValueError:
                QMessageBox.warning(self, "Invalid Temperature", "Temperature must be a number.")
                return

        path = save_hermes_config(
            {
                "provider": self.provider_input.currentData() or "",
                "api_key": self.api_key_input.text().strip(),
                "base_url": self.base_url_input.text().strip(),
                "temperature": temperature,
            }
        )
        next_server_url = self.web_server_input.text().strip()
        web_updates = {
            "web_mode_enabled": self.web_mode_input.isChecked(),
            "server_url": next_server_url,
        }
        if next_server_url.strip().rstrip("/") != load_web_config().get("server_url", "").strip().rstrip("/"):
            web_updates.update({"auth_token": "", "username": ""})
        save_web_config(web_updates)
        paths = describe_hermes_paths(create=True)
        self.config_path_label.setText(
            f"Hermes: {paths['executable']}\n"
            f"Config: {path}\n"
            f"Secrets: {paths['env']}"
        )
        parent = self.parent()
        chat_panel = getattr(parent, "chat_panel", None)
        if chat_panel is not None and hasattr(chat_panel, "reload_connection_mode"):
            chat_panel.reload_connection_mode()
        self.accept()

    def refresh_web_account_row(self):
        web_config = load_web_config()
        username = str(web_config.get("username", "") or "").strip()
        token = str(web_config.get("auth_token", "") or "").strip()
        if username and token:
            self.web_account_label.setText(f"Signed in as {username}")
            self.web_logout_button.setEnabled(True)
        else:
            self.web_account_label.setText("Not signed in")
            self.web_logout_button.setEnabled(False)

    def open_web_login(self):
        save_web_config(
            {
                "web_mode_enabled": self.web_mode_input.isChecked(),
                "server_url": self.web_server_input.text().strip(),
            }
        )
        dialog = WebLoginDialog(self)
        if dialog.exec():
            web_config = load_web_config()
            self.web_server_input.setText(web_config.get("server_url", ""))
            self.web_mode_input.setChecked(True)
            self.refresh_web_account_row()
            parent = self.parent()
            chat_panel = getattr(parent, "chat_panel", None)
            if chat_panel is not None and hasattr(chat_panel, "reload_connection_mode"):
                chat_panel.reload_connection_mode()

    def logout_web_account(self):
        result = QMessageBox.question(
            self,
            "Logout",
            "Log out of this remote Anna server account?",
        )
        if result != QMessageBox.Yes:
            return

        try:
            WebAgentClient().logout()
        except Exception:
            save_web_config({"auth_token": "", "username": ""})

        self.refresh_web_account_row()
        parent = self.parent()
        chat_panel = getattr(parent, "chat_panel", None)
        if chat_panel is not None and hasattr(chat_panel, "reload_connection_mode"):
            chat_panel.reload_connection_mode()

    def export_memory(self):
        self.set_memory_buttons_enabled(False)
        if self.is_web_mode_selected():
            self.loading_overlay.start()
            self.memory_status_label.setText("Exporting server memory and RAG...")
            worker = threading.Thread(
                target=self._export_memory_worker,
                args=(self.remote_client(),),
                daemon=True,
            )
        else:
            self.memory_status_label.setText("Exporting memory...")
            worker = threading.Thread(target=self._export_memory_worker, daemon=True)
        worker.start()

    def _export_memory_worker(self, client: WebAgentClient = None):
        try:
            if client is not None:
                path = client.export_archive(get_downloads_dir())
                self.memory_operation_finished.emit(
                    "Server Memory Exported",
                    f"Server Hermes memory and server RAG were exported to:\n\n{path}",
                )
            else:
                path = export_memory_archive()
                self.memory_operation_finished.emit(
                    "Memory Exported",
                    f"Hermes memory and Anna RAG were exported to:\n\n{path}",
                )
        except Exception as e:
            self.memory_operation_failed.emit(str(e))

    def import_memory(self):
        dialog = QFileDialog(self, "Import Hermes Memory Archive", get_downloads_dir())
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setNameFilters(
            [
                "Anna Memory Archives (*.ana)",
                "Legacy Zip Archives (*.zip)",
                "All Files (*)",
            ]
        )
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        if not dialog.exec():
            return

        selected_files = dialog.selectedFiles()
        path = selected_files[0] if selected_files else ""
        if not path:
            return

        result = QMessageBox.question(
            self,
            "Import Memory",
            self.import_confirmation_text(),
        )
        if result != QMessageBox.Yes:
            return

        self.set_memory_buttons_enabled(False)
        if self.is_web_mode_selected():
            self.loading_overlay.start()
            self.memory_status_label.setText("Importing server memory and RAG...")
            worker = threading.Thread(
                target=self._import_memory_worker,
                args=(path, self.remote_client()),
                daemon=True,
            )
        else:
            self.memory_status_label.setText("Importing memory...")
            worker = threading.Thread(target=self._import_memory_worker, args=(path,), daemon=True)
        worker.start()

    def _import_memory_worker(self, path: str, client: WebAgentClient = None):
        try:
            if client is not None:
                result = client.import_archive(path)
                message = (
                    "Server memory and RAG import completed.\n\n"
                    f"Server backup:\n{result.get('backup_path') or '(none)'}\n\n"
                    f"Imported memory document:\n{result.get('imported_memory_path') or '(none found)'}\n\n"
                    f"Imported files: {result.get('imported_files', 0)}\n"
                    f"Imported RAG chunks: {result.get('imported_chunks', 0)}"
                )
                self.memory_operation_finished.emit("Server Memory Imported", message)
            else:
                backup_path, imported_memory_path = import_memory_archive(path)
                message = (
                    "Memory import completed.\n\n"
                    f"Backup export:\n{backup_path}\n\n"
                    f"Imported memory document:\n{imported_memory_path or '(none found)'}"
                )
                self.memory_operation_finished.emit("Memory Imported", message)
        except Exception as e:
            self.memory_operation_failed.emit(str(e))

    def open_memory_recovery(self):
        if self.is_web_mode_selected():
            dialog = RemoteMemoryRecoveryDialog(self.remote_client(), self)
        else:
            dialog = MemoryRecoveryDialog(self)
        self.recovery_dialog = dialog
        dialog.show()

    def set_memory_buttons_enabled(self, enabled: bool):
        self.export_memory_button.setEnabled(enabled)
        self.import_memory_button.setEnabled(enabled)
        self.recovery_button.setEnabled(enabled)

    def handle_memory_operation_finished(self, title: str, message: str):
        self.loading_overlay.stop()
        self.set_memory_buttons_enabled(True)
        self.memory_status_label.setText("")
        QMessageBox.information(self, title, message)

    def handle_memory_operation_failed(self, message: str):
        self.loading_overlay.stop()
        self.set_memory_buttons_enabled(True)
        self.memory_status_label.setText("")
        QMessageBox.warning(self, "Memory Operation Failed", message)

    def is_web_mode_selected(self) -> bool:
        return self.web_mode_input.isChecked()

    def remote_client(self) -> WebAgentClient:
        return WebAgentClient(server_url=self.web_server_input.text().strip())

    def import_confirmation_text(self) -> str:
        if self.is_web_mode_selected():
            return (
                "Anna will ask the server to export a backup of current server memory and RAG, "
                "then import the selected archive into the server. Continue?"
            )
        return (
            "Anna will first export a backup of the current memory, then import the selected memory and RAG archive. Continue?"
        )


class HermesMemoryDialog(QDialog):
    memory_operation_finished = Signal(str, str)
    memory_operation_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Hermes Memory")
        self.setModal(False)
        self.resize(760, 560)
        self.setStyleSheet(DIALOG_STYLE)

        self.memory_text = QTextEdit()
        self.memory_text.setReadOnly(True)

        refresh_button = QPushButton("Refresh Memory")
        refresh_button.clicked.connect(self.refresh_memory)
        self.refresh_button = refresh_button

        clear_button = QPushButton("Clear Memory")
        clear_button.clicked.connect(self.confirm_clear_memory)
        self.clear_button = clear_button

        recovery_button = QPushButton("Recovery")
        recovery_button.clicked.connect(self.open_memory_recovery)
        self.recovery_button = recovery_button

        self.memory_status_label = QLabel("")
        self.memory_status_label.setObjectName("memoryOperationStatus")

        button_row = QHBoxLayout()
        button_row.addWidget(refresh_button)
        button_row.addWidget(clear_button)
        button_row.addWidget(recovery_button)
        button_row.addStretch(1)

        self.memory_operation_finished.connect(self.handle_memory_operation_finished)
        self.memory_operation_failed.connect(self.handle_memory_operation_failed)

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(self.memory_status_label)
        layout.addWidget(self.memory_text)
        self.setLayout(layout)

        self.refresh_memory()

    def refresh_memory(self):
        self.memory_text.setPlainText(read_hermes_memory())

    def confirm_clear_memory(self):
        result = QMessageBox.question(
            self,
            "Clear Memory",
            "Anna will first create a backup, then clear Hermes memory. Continue?",
        )
        if result != QMessageBox.Yes:
            return

        self.set_memory_buttons_enabled(False)
        self.memory_status_label.setText("Clearing memory...")
        worker = threading.Thread(target=self._clear_memory_worker, daemon=True)
        worker.start()

    def _clear_memory_worker(self):
        try:
            backup_path = clear_hermes_memory_with_backup()
            self.memory_operation_finished.emit(
                "Memory Cleared",
                f"Hermes memory was cleared.\n\nBackup saved to:\n{backup_path}",
            )
        except Exception as e:
            self.memory_operation_failed.emit(str(e))

    def open_memory_recovery(self):
        dialog = MemoryRecoveryDialog(self)
        self.recovery_dialog = dialog
        dialog.memory_restored.connect(self.refresh_memory)
        dialog.show()

    def set_memory_buttons_enabled(self, enabled: bool):
        self.refresh_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.recovery_button.setEnabled(enabled)

    def handle_memory_operation_finished(self, title: str, message: str):
        self.set_memory_buttons_enabled(True)
        self.memory_status_label.setText("")
        self.refresh_memory()
        QMessageBox.information(self, title, message)

    def handle_memory_operation_failed(self, message: str):
        self.set_memory_buttons_enabled(True)
        self.memory_status_label.setText("")
        QMessageBox.warning(self, "Memory Operation Failed", message)


class HermesSkillsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Hermes Skills")
        self.setModal(False)
        self.resize(860, 600)
        self.setStyleSheet(DIALOG_STYLE + SKILLS_GRID_STYLE)

        self.skills_grid = SkillsGridWidget()

        refresh_button = QPushButton("Refresh Skills")
        refresh_button.clicked.connect(self.refresh_skills)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.skills_grid, stretch=1)
        self.setLayout(layout)

        self.refresh_skills()

    def refresh_skills(self):
        self.skills_grid.set_skills_from_text(read_hermes_skills_text())
