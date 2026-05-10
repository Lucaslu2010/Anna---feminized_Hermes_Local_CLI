import json
import os
from typing import Optional, List
from paths import resource_path
from PySide6.QtCore import QUrl, Slot, QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from terminal_backend import TerminalBackend

SEND_NUL_HEARTBEAT = False


class WebTerminalPanel(QWebEngineView):
    """
    xterm.js frontend + Python PTY backend panel.

    PTY output is buffered and flushed by a QTimer.
    This prevents QtWebEngine from freezing when Hermes outputs frequent chunks.
    """

    def __init__(self, command: Optional[List[str]] = None):
        super().__init__()

        self.backend = TerminalBackend(command=command)

        self.channel = QWebChannel(self.page())
        self.channel.registerObject("terminalBackend", self.backend)
        self.page().setWebChannel(self.channel)

        self.pending_output = []
        self.max_pending_chunks = 500

        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(30)
        self.flush_timer.timeout.connect(self.flush_terminal_output)
        self.flush_timer.start()

        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(500)
        self.heartbeat_timer.timeout.connect(self.force_terminal_refresh)
        self.heartbeat_timer.start()

        self.backend.output_received.connect(self.queue_terminal_output)
        self.backend.error_received.connect(self.write_error_to_terminal)
        self.backend.rag_status_received.connect(self.show_rag_status)

        html_path = resource_path("web/terminal.html")

        if not os.path.exists(html_path):
            raise FileNotFoundError(f"Cannot find terminal.html: {html_path}")

        self.loadFinished.connect(self.handle_load_finished)
        self.load(QUrl.fromLocalFile(html_path))

    @Slot(bool)
    def handle_load_finished(self, ok: bool):
        if not ok:
            print("Failed to load terminal.html")

        # Do NOT start backend here if your terminal.html already calls:
        # pyBackend.start_process()
        #
        # If your terminal.html does not call start_process(), uncomment this:
        # self.backend.start()

    @Slot(str)
    def queue_terminal_output(self, text: str):
        """
        Called when PTY produces output.
        Do not call runJavaScript directly here.
        Queue it and let QTimer flush it.
        """

        if not text:
            return

        self.pending_output.append(text)

        # Prevent unbounded memory growth if Hermes floods output.
        if len(self.pending_output) > self.max_pending_chunks:
            self.pending_output = self.pending_output[-self.max_pending_chunks:]

    @Slot()
    def flush_terminal_output(self):
        if not self.pending_output:
            return

        text = "".join(self.pending_output)
        self.pending_output.clear()

        js = f"""
        if (window.writeToTerminal) {{
            window.writeToTerminal({json.dumps(text)});
        }}
        """

        self.page().runJavaScript(js)

    @Slot()
    def force_terminal_refresh(self):
        self.flush_terminal_output()

        if SEND_NUL_HEARTBEAT and self.backend:
            self.backend.send_nul_heartbeat()

    @Slot(str)
    def write_error_to_terminal(self, text: str):
        error_text = "\r\n[ERROR] " + text + "\r\n"
        self.pending_output.append(error_text)

    @Slot(str)
    def show_rag_status(self, text: str):
        js = f"""
        if (window.setRagStatus) {{
            window.setRagStatus({json.dumps(text)});
        }}
        """
        self.page().runJavaScript(js)

    def stop(self):
        self.flush_timer.stop()

        if hasattr(self, "heartbeat_timer"):
            self.heartbeat_timer.stop()

        self.backend.stop()
