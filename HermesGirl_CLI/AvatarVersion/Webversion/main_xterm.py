import sys
import time
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QStackedWidget,
)
from paths import resource_path
from avatar_event_filter import detect_avatar_state_from_terminal
from gateway_chat_panel import GatewayChatPanel
from avatar_manager import AvatarPanel
from hermes_settings_dialog import (
    HermesMemoryDialog,
    HermesSettingsDialog,
    HermesSkillsDialog,
)
from rag_manager_dialog import RagManagerDialog
from remote_rag_manager_dialog import RemoteFileManagerDialog
from remote_text_dialog import RemoteTextDialog
from web_agent_client import WebAgentClient
from web_login_dialog import ensure_web_login
from web_settings import is_web_mode_enabled


WINDOW_TITLE = "Anna"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(QIcon(resource_path("assets/icon.png")))
        self.resize(1300, 780)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #ffffff;
            }

            QSplitter {
                background-color: #ffffff;
            }

            QSplitter::handle {
                background-color: rgba(230, 230, 230, 160);
                width: 1px;
            }
        """)

        self.has_real_agent_output = False

        self.current_avatar_state = "idle"
        self.last_avatar_state_change = 0.0
        self.avatar_min_hold_seconds = 0.8
        self.avatar_talking_min_hold_seconds = 5.0
        self.pending_avatar_state = ""

        self.suppress_talking_until = 0.0
        self.last_user_input_time = 0.0
        self.last_protocol_state_time = 0.0

        self.chat_panel = GatewayChatPanel()
        self.avatar_panel = AvatarPanel()
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.chat_panel)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.content_stack)
        splitter.addWidget(self.avatar_panel)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        self.avatar_idle_timer = QTimer(self)
        self.avatar_idle_timer.setSingleShot(True)
        self.avatar_idle_timer.timeout.connect(self.set_avatar_idle)

        self.avatar_pending_timer = QTimer(self)
        self.avatar_pending_timer.setSingleShot(True)
        self.avatar_pending_timer.timeout.connect(self.apply_pending_avatar_state)

        self.chat_panel.avatar_state_received.connect(self.handle_avatar_state_event)
        self.chat_panel.user_input_received.connect(self.handle_user_input_activity)
        self.chat_panel.assistant_output_received.connect(self.handle_gateway_output)
        self.chat_panel.error_received.connect(self.handle_runtime_error)

        self.setup_avatar_buttons()
        self.set_avatar_idle()

    def setup_avatar_buttons(self):
        self.avatar_panel.add_panel_button(
            "Files",
            self.open_file_manager,
        )
        self.avatar_panel.add_panel_button(
            "Hermes",
            self.open_hermes_settings,
        )
        self.avatar_panel.add_panel_button(
            "Memory",
            self.open_hermes_memory,
        )
        self.avatar_panel.add_panel_button(
            "Skills",
            self.open_hermes_skills,
        )

    @Slot()
    def open_file_manager(self):
        dialog = RemoteFileManagerDialog(self) if is_web_mode_enabled() else RagManagerDialog(self)
        dialog.exec()

    @Slot()
    def open_rag_manager(self):
        self.open_file_manager()

    @Slot()
    def open_embedding_settings(self):
        self.open_rag_manager()

    @Slot()
    def show_gateway_chat(self):
        self.content_stack.setCurrentWidget(self.chat_panel)

    @Slot()
    def open_hermes_settings(self):
        dialog = HermesSettingsDialog(self)
        dialog.exec()

    @Slot()
    def open_hermes_memory(self):
        if is_web_mode_enabled():
            dialog = RemoteTextDialog(
                "Server Account Memory",
                WebAgentClient().get_memory,
                self,
            )
        else:
            dialog = HermesMemoryDialog(self)
        dialog.exec()

    @Slot()
    def open_hermes_skills(self):
        if is_web_mode_enabled():
            dialog = RemoteTextDialog(
                "Server Hermes Skills",
                WebAgentClient().get_skills,
                self,
            )
        else:
            dialog = HermesSkillsDialog(self)
        dialog.exec()

    def set_avatar_idle(self):
        self.avatar_panel.update_to_idle()
        self.current_avatar_state = "idle"
        self.last_avatar_state_change = time.monotonic()
        self.pending_avatar_state = ""

    def avatar_state_priority(self, state: str) -> int:
        priorities = {
            "idle": 0,
            "sleep": 0,
            "listening": 1,
            "thinking": 2,
            "talking": 3,
            "happy": 3,
            "explain": 4,
            "success": 5,
            "coding": 2,
            "searching": 7,
            "warning": 8,
        }

        return priorities.get(state, 0)

    def set_avatar_state_safely(self, new_state: str):
        new_state = self.normalize_avatar_state(new_state)
        if not new_state:
            return False

        now = time.monotonic()

        old_state = self.current_avatar_state

        if new_state == old_state:
            if new_state in ["talking", "explain", "thinking", "listening", "searching", "coding"]:
                self.pending_avatar_state = ""
            return True

        if self.should_hold_current_state(old_state, new_state, now):
            self.defer_avatar_state(new_state, old_state, now)
            return False

        old_priority = self.avatar_state_priority(old_state)
        new_priority = self.avatar_state_priority(new_state)

        elapsed = now - self.last_avatar_state_change

        # High priority state can always override low priority state.
        if new_priority > old_priority:
            self.avatar_panel.update_avatar(new_state)
            self.current_avatar_state = new_state
            self.last_avatar_state_change = now
            self.pending_avatar_state = ""
            return True

        # Do not let thinking overwrite active high-value states.
        if new_state == "thinking" and old_state in [
            "searching",
            "warning",
            "explain",
            "success",
        ]:
            return False

        # Do not switch too fast between ordinary states.
        if elapsed < self.avatar_min_hold_seconds:
            return False

        self.avatar_panel.update_avatar(new_state)
        self.current_avatar_state = new_state
        self.last_avatar_state_change = now
        self.pending_avatar_state = ""
        return True

    def set_avatar_state_from_protocol(self, new_state: str):
        new_state = self.normalize_avatar_state(new_state)
        if not new_state:
            return

        if new_state == "idle":
            self.schedule_avatar_idle_for_current_state()
            return

        applied = self.set_avatar_state_safely(new_state)

        if applied and new_state not in ["thinking", "listening"]:
            self.avatar_idle_timer.start(self.idle_delay_for_state(new_state))

    def normalize_avatar_state(self, state: str) -> str:
        state = (state or "").strip().lower()
        if state == "explaining":
            return "explain"
        return state

    def should_hold_current_state(self, old_state: str, new_state: str, now: float) -> bool:
        if old_state not in ["talking", "explain"]:
            return False

        if new_state not in ["idle", "success", "happy"]:
            return False

        elapsed = now - self.last_avatar_state_change
        return elapsed < self.avatar_talking_min_hold_seconds

    def defer_avatar_state(self, new_state: str, old_state: str, now: float):
        remaining = self.avatar_talking_min_hold_seconds - (now - self.last_avatar_state_change)
        delay_ms = max(0, int(remaining * 1000))
        self.pending_avatar_state = new_state
        self.avatar_pending_timer.start(delay_ms)

    @Slot()
    def apply_pending_avatar_state(self):
        state = self.pending_avatar_state
        self.pending_avatar_state = ""
        if not state:
            return

        if state == "idle":
            self.set_avatar_idle()
        else:
            applied = self.set_avatar_state_safely(state)
            if applied and state not in ["thinking", "listening"]:
                self.avatar_idle_timer.start(self.idle_delay_for_state(state))

    def schedule_avatar_idle_for_current_state(self, base_delay_ms: int = 0):
        now = time.monotonic()
        delay_ms = base_delay_ms
        if self.current_avatar_state in ["talking", "explain"]:
            remaining = self.avatar_talking_min_hold_seconds - (
                now - self.last_avatar_state_change
            )
            delay_ms = max(delay_ms, int(max(0.0, remaining) * 1000))

        self.pending_avatar_state = "idle"
        self.avatar_pending_timer.start(max(0, delay_ms))

    def idle_delay_for_state(self, state: str) -> int:
        if state in ["talking", "explain"]:
            return int(self.avatar_talking_min_hold_seconds * 1000)
        if state in ["success", "happy"]:
            return 4000
        if state in ["warning", "searching", "coding"]:
            return 4500
        return 2500

    @Slot(str)
    def handle_avatar_state_event(self, state: str):
        self.last_protocol_state_time = time.monotonic()
        self.set_avatar_state_from_protocol(state)

    @Slot(str)
    def handle_user_input_activity(self, data: str):
        """
        Called whenever the user sends text through the chat composer.

        We temporarily suppress 'talking' because echoes of user input
        can otherwise be misclassified as Hermes speaking.
        """

        if not data:
            return

        self.last_user_input_time = time.monotonic()

        # Suppress generic talking state for a short time after user input.
        self.suppress_talking_until = time.monotonic() + 0.8

        # Do not switch avatar for pure control keys.
        control_inputs = {
            "\r",
            "\n",
            "\x7f",
            "\x03",
            "\x04",
            "\t",
            "\x1b[A",
            "\x1b[B",
            "\x1b[C",
            "\x1b[D",
        }

        if data in control_inputs:
            return

        self.set_avatar_state_safely("listening")

    @Slot(str)
    def handle_gateway_output(self, text: str):
        if not text:
            return

        if time.monotonic() - self.last_protocol_state_time < 0.6:
            return

        state = detect_avatar_state_from_terminal(text)
        if state:
            self.set_avatar_state_safely(state)

        if state not in [None, "thinking", "listening"]:
            self.has_real_agent_output = True
            self.avatar_idle_timer.start(self.idle_delay_for_state(state))

    @Slot(str)
    def handle_runtime_error(self, error_text: str):
        self.set_avatar_state_safely("warning")
        print(error_text)

    def closeEvent(self, event):
        if self.chat_panel:
            self.chat_panel.stop()

        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("assets/icon.png")))
    if is_web_mode_enabled() and not ensure_web_login():
        sys.exit(0)
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
