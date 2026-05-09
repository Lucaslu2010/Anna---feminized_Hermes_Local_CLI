import sys
import time
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
)
from hermes_locator import build_hermes_command
from avatar_event_filter import detect_avatar_state_from_terminal
from web_terminal_panel import WebTerminalPanel
from avatar_manager import AvatarPanel



WINDOW_TITLE = "Anna"
#HERMES_COMMAND = ["/bin/bash", "-lc", "for i in {1..20}; do echo tick-$i; sleep 1; done; exec /bin/bash -i"]
try:
    HERMES_COMMAND = build_hermes_command(use_yolo=False)
except FileNotFoundError:
    HERMES_COMMAND = None

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(QIcon("assets/icon.png"))
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

        self.suppress_talking_until = 0.0
        self.last_user_input_time = 0.0

        self.terminal_panel = WebTerminalPanel(HERMES_COMMAND or ["hermes"])
        self.avatar_panel = AvatarPanel()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.terminal_panel)
        splitter.addWidget(self.avatar_panel)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        self.avatar_idle_timer = QTimer(self)
        self.avatar_idle_timer.setSingleShot(True)
        self.avatar_idle_timer.timeout.connect(self.avatar_panel.update_to_idle)

        self.terminal_panel.backend.raw_output_received.connect(self.handle_terminal_output)
        self.terminal_panel.backend.user_input_received.connect(self.handle_user_input_activity)
        self.terminal_panel.backend.process_started.connect(self.handle_terminal_started)
        self.terminal_panel.backend.process_stopped.connect(self.handle_terminal_stopped)
        self.terminal_panel.backend.error_received.connect(self.handle_terminal_error)

        self.avatar_panel.update_to_idle()

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
        if not new_state:
            return

        now = time.monotonic()

        old_state = self.current_avatar_state

        if new_state == old_state:
            return

        old_priority = self.avatar_state_priority(old_state)
        new_priority = self.avatar_state_priority(new_state)

        elapsed = now - self.last_avatar_state_change

        # High priority state can always override low priority state.
        if new_priority > old_priority:
            self.avatar_panel.update_avatar(new_state)
            self.current_avatar_state = new_state
            self.last_avatar_state_change = now
            return

        # Do not let thinking overwrite active high-value states.
        if new_state == "thinking" and old_state in [
            "searching",
            "warning",
            "explain",
            "success",
        ]:
            return

        # Do not switch too fast between ordinary states.
        if elapsed < self.avatar_min_hold_seconds:
            return

        self.avatar_panel.update_avatar(new_state)
        self.current_avatar_state = new_state
        self.last_avatar_state_change = now

    @Slot(str)
    def handle_user_input_activity(self, data: str):
        """
        Called whenever the user types into xterm.

        We temporarily suppress 'talking' because terminal echo of user input
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

    @Slot()
    def handle_terminal_started(self):
        self.avatar_panel.update_to_idle()

    @Slot()
    def handle_terminal_stopped(self):
        self.avatar_panel.update_to_idle()

    @Slot(str)
    def handle_terminal_output(self, raw_text: str):
        if not raw_text:
            return

        state = detect_avatar_state_from_terminal(raw_text)

        if state is None:
            return

        # If this is user echo, suppress weak talking.
        if state == "talking" and hasattr(self, "suppress_talking_until"):
            if time.monotonic() < self.suppress_talking_until:
                return

        self.set_avatar_state_safely(state)

        if state not in ["thinking", "listening"]:
            self.has_real_agent_output = True
            self.avatar_idle_timer.start(2500)
    @Slot(str)
    def handle_terminal_error(self, error_text: str):
        self.avatar_panel.set_avatar_state_safely("warning")
        print(error_text)

    def closeEvent(self, event):
        if self.terminal_panel:
            self.terminal_panel.stop()

        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("assets/icon.png"))
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()