import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
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

    QListWidget {
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

    QLabel#remoteMemoryStatus {
        color: #57606a;
        font-size: 12px;
    }
"""


class RemoteMemoryRecoveryDialog(QDialog):
    backups_loaded = Signal(list)
    operation_finished = Signal(str, str)
    operation_failed = Signal(str)
    memory_restored = Signal()

    def __init__(self, client: WebAgentClient = None, parent=None):
        super().__init__(parent)

        self.client = client or WebAgentClient()
        self.setWindowTitle("Server Memory Recovery")
        self.setModal(False)
        self.resize(720, 420)
        self.setStyleSheet(STYLE)

        self.backup_list = QListWidget()
        self.backup_list.setAlternatingRowColors(True)

        self.status_label = QLabel("")
        self.status_label.setObjectName("remoteMemoryStatus")

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
        layout.addWidget(QLabel("Available server memory and RAG backups"))
        layout.addWidget(self.backup_list)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.loading_overlay = CloudLoadingOverlay(self)

        self.backups_loaded.connect(self.handle_backups_loaded)
        self.operation_finished.connect(self.handle_operation_finished)
        self.operation_failed.connect(self.handle_operation_failed)
        self.refresh_backups()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.loading_overlay.setGeometry(self.rect())

    def refresh_backups(self):
        self.status_label.setText("Loading server backups...")
        self.set_operation_buttons_enabled(False)
        self.loading_overlay.start()
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            self.backups_loaded.emit(self.client.list_backups())
        except Exception as e:
            self.operation_failed.emit(str(e))

    def handle_backups_loaded(self, backups):
        self.loading_overlay.stop()
        self.backup_list.clear()
        for backup in backups or []:
            time_text = format_backup_time(
                str(backup.get("created_at", "")),
                float(backup.get("mtime", 0) or 0),
            )
            item = QListWidgetItem(f"{backup.get('name', '')}\nBackup time: {time_text}")
            item.setData(Qt.UserRole, backup.get("name", ""))
            self.backup_list.addItem(item)

        has_backups = bool(backups)
        self.set_operation_buttons_enabled(True)
        self.restore_button.setEnabled(has_backups)
        self.delete_all_button.setEnabled(has_backups)
        self.status_label.setText("" if has_backups else "No server backups found.")

    def restore_selected_backup(self):
        item = self.backup_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Restore Memory", "Select a backup first.")
            return

        name = item.data(Qt.UserRole)
        self.set_operation_buttons_enabled(False)
        self.status_label.setText("Restoring server memory and RAG...")
        self.loading_overlay.start()
        threading.Thread(target=self._restore_worker, args=(name,), daemon=True).start()

    def _restore_worker(self, name: str):
        try:
            result = self.client.restore_backup(name)
            message = (
                "Server memory and RAG restore completed.\n\n"
                f"Backup before restore:\n{result.get('backup_path') or '(none)'}\n\n"
                f"Imported memory document:\n{result.get('imported_memory_path') or '(none found)'}\n\n"
                f"Imported files: {result.get('imported_files', 0)}\n"
                f"Imported RAG chunks: {result.get('imported_chunks', 0)}"
            )
            self.operation_finished.emit("Server Memory Restored", message)
        except Exception as e:
            self.operation_failed.emit(str(e))

    def confirm_delete_all_backups(self):
        result = QMessageBox.question(
            self,
            "Clear Server Backups",
            "Delete all server memory and RAG backups? This cannot be undone.",
        )
        if result != QMessageBox.Yes:
            return

        self.set_operation_buttons_enabled(False)
        self.status_label.setText("Deleting server backups...")
        self.loading_overlay.start()
        threading.Thread(target=self._delete_all_worker, daemon=True).start()

    def _delete_all_worker(self):
        try:
            result = self.client.delete_all_backups()
            deleted = int(result.get("deleted", 0) or 0)
            self.operation_finished.emit(
                "Server Backups Cleared",
                f"Deleted {deleted} server backup{'s' if deleted != 1 else ''}.",
            )
        except Exception as e:
            self.operation_failed.emit(str(e))

    def set_operation_buttons_enabled(self, enabled: bool):
        self.restore_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.delete_all_button.setEnabled(enabled)

    def handle_operation_finished(self, title: str, message: str):
        self.loading_overlay.stop()
        self.set_operation_buttons_enabled(True)
        self.status_label.setText("")
        self.memory_restored.emit()
        self.refresh_backups()
        QMessageBox.information(self, title, message)

    def handle_operation_failed(self, message: str):
        self.loading_overlay.stop()
        self.set_operation_buttons_enabled(True)
        self.status_label.setText(f"Server operation failed: {message}")
        QMessageBox.warning(self, "Server Memory Operation Failed", message)


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
