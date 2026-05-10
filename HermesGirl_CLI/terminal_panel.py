import os
import pty
import signal
import subprocess
import threading
import select
import re
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QFont, QTextCursor, QKeyEvent
from PySide6.QtWidgets import QTextEdit, QWidget, QVBoxLayout, QApplication


ANSI_RE = re.compile(
    r"""
    \x1B
    (?:
        [@-Z\\-_]
        |
        \[
        [0-?]*
        [ -/]*
        [@-~]
    )
    """,
    re.VERBOSE,
)


def strip_ansi(text: str) -> str:
    if not text:
        return ""
    return ANSI_RE.sub("", text)


class TerminalView(QTextEdit):
    """
    Terminal-like display.

    This is not a full xterm emulator.
    It is a stable CLI-style view with:
    - scrollback
    - visible cursor
    - visible user typing line
    - keyboard forwarding to PTY
    """

    key_data = Signal(bytes)
    user_entered_line = Signal(str)

    def __init__(self):
        super().__init__()

        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.NoWrap)

        font = QFont("Menlo")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(13)
        self.setFont(font)

        self.setStyleSheet("""
            QTextEdit {
                background-color: #050505;
                color: #e6edf3;
                border: none;
                padding: 12px;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }

            QScrollBar:vertical {
                background: #050505;
                width: 10px;
            }

            QScrollBar::handle:vertical {
                background: #30363d;
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        self.output_text = ""
        self.current_input = ""
        self.prompt = "\n> "
        self.cursor_visible = True

        self.cursor_timer = QTimer(self)
        self.cursor_timer.timeout.connect(self.toggle_cursor)
        self.cursor_timer.start(500)

        self.max_chars = 120000
        self.user_is_scrolling = False

        self.verticalScrollBar().valueChanged.connect(self.on_scroll_changed)

        self.render()

    def on_scroll_changed(self):
        bar = self.verticalScrollBar()
        self.user_is_scrolling = bar.value() < bar.maximum() - 5

    def toggle_cursor(self):
        self.cursor_visible = not self.cursor_visible
        self.render(keep_scroll=True)

    def cursor_symbol(self) -> str:
        return "█" if self.cursor_visible else " "

    def render(self, keep_scroll: bool = False):
        bar = self.verticalScrollBar()
        old_value = bar.value()
        old_max = bar.maximum()

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)

        display = self.output_text

        if not display.endswith("\n"):
            display += "\n"

        display += "> " + self.current_input + self.cursor_symbol()

        self.setPlainText(display)

        if keep_scroll and self.user_is_scrolling:
            new_max = self.verticalScrollBar().maximum()
            delta = new_max - old_max
            self.verticalScrollBar().setValue(old_value + delta)
        else:
            self.scroll_to_bottom()

    def scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_output(self, text: str):
        if not text:
            return

        cleaned = text
        cleaned = cleaned.replace("\r\n", "\n")
        cleaned = cleaned.replace("\r", "\n")

        if not cleaned:
            return

        self.output_text += cleaned

        if len(self.output_text) > self.max_chars:
            self.output_text = self.output_text[-self.max_chars:]

        self.render()

    def clear_current_input(self):
        self.current_input = ""
        self.render()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()

        # Copy
        if modifiers & Qt.ControlModifier and key == Qt.Key_C:
            if self.textCursor().hasSelection():
                self.copy()
            else:
                self.key_data.emit(b"\x03")
                self.current_input = ""
                self.render()
            return

        # Paste
        if modifiers & Qt.ControlModifier and key == Qt.Key_V:
            text = QApplication.clipboard().text()
            if text:
                self.key_data.emit(text.encode("utf-8", errors="replace"))
                self.current_input += text
                self.render()
            return

        # Ctrl+D
        if modifiers & Qt.ControlModifier and key == Qt.Key_D:
            self.key_data.emit(b"\x04")
            return

        # Ctrl+L
        if modifiers & Qt.ControlModifier and key == Qt.Key_L:
            self.output_text = ""
            self.current_input = ""
            self.key_data.emit(b"\x0c")
            self.render()
            return

        # Enter
        if key in (Qt.Key_Return, Qt.Key_Enter):
            line = self.current_input

            self.output_text += "\n> " + line + "\n"
            self.current_input = ""
            self.render()

            self.key_data.emit(line.encode("utf-8", errors="replace") + b"\r")

            if line.strip():
                self.user_entered_line.emit(line.strip())

            return

        # Backspace
        if key == Qt.Key_Backspace:
            if self.current_input:
                self.current_input = self.current_input[:-1]
                self.key_data.emit(b"\x7f")
                self.render()
            return

        # Tab
        if key == Qt.Key_Tab:
            self.current_input += "\t"
            self.key_data.emit(b"\t")
            self.render()
            return

        # Arrow keys: send to Hermes, do not change visible input manually.
        if key == Qt.Key_Up:
            self.key_data.emit(b"\x1b[A")
            return

        if key == Qt.Key_Down:
            self.key_data.emit(b"\x1b[B")
            return

        if key == Qt.Key_Right:
            self.key_data.emit(b"\x1b[C")
            return

        if key == Qt.Key_Left:
            self.key_data.emit(b"\x1b[D")
            return

        if key == Qt.Key_Escape:
            self.key_data.emit(b"\x1b")
            return

        # Normal printable input
        text = event.text()
        if text:
            self.current_input += text
            self.key_data.emit(text.encode("utf-8", errors="replace"))
            self.render()
            return

        super().keyPressEvent(event)


class TerminalPanel(QWidget):
    raw_output_received = Signal(str)
    screen_output_received = Signal(str)
    user_line_submitted = Signal(str)
    process_started = Signal()
    process_stopped = Signal()
    error_received = Signal(str)

    def __init__(
        self,
        command: Optional[List[str]] = None,
        cols: int = 120,
        rows: int = 36,
    ):
        super().__init__()

        self.command = command or ["hermes"]
        self.cols = cols
        self.rows = rows

        self.master_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.running = False

        self.terminal_view = TerminalView()
        self.terminal_view.key_data.connect(self.write_to_terminal)
        self.terminal_view.user_entered_line.connect(self.user_line_submitted)

        self.screen_output_received.connect(self.terminal_view.append_output)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.terminal_view)
        self.setLayout(layout)

        self.setStyleSheet("""
            QWidget {
                background-color: #050505;
            }
        """)

    def start(self):
        if self.running:
            return

        try:
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLUMNS"] = str(self.cols)
            env["LINES"] = str(self.rows)

            self.process = subprocess.Popen(
                self.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )

            os.close(slave_fd)

            self.running = True
            self.reader_thread = threading.Thread(
                target=self._reader_loop,
                daemon=True,
            )
            self.reader_thread.start()

            self.process_started.emit()

        except FileNotFoundError:
            self.error_received.emit(
                f"Cannot find command: {self.command}\n"
                "Check your Hermes command path."
            )
        except Exception as e:
            self.error_received.emit(f"Failed to start terminal process:\n{e}")

    def stop(self):
        self.running = False

        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except Exception:
                try:
                    self.process.terminate()
                except Exception:
                    pass

            try:
                self.process.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass

            self.master_fd = None

        self.process_stopped.emit()

    def _reader_loop(self):
        while self.running and self.master_fd is not None:
            try:
                readable, _, _ = select.select([self.master_fd], [], [], 0.05)

                if not readable:
                    continue

                data = os.read(self.master_fd, 4096)

                if not data:
                    break

                text = data.decode("utf-8", errors="replace")

                self.raw_output_received.emit(text)
                self.screen_output_received.emit(text)

            except OSError:
                break
            except Exception as e:
                self.error_received.emit(f"Terminal read error:\n{e}")
                break

        self.running = False
        self.process_stopped.emit()

    @Slot(bytes)
    def write_to_terminal(self, data: bytes):
        if not self.running or self.master_fd is None:
            return

        try:
            os.write(self.master_fd, data)
        except Exception as e:
            self.error_received.emit(f"Failed to write to terminal:\n{e}")