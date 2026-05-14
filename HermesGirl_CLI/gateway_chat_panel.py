import re
import threading

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from file_text_extractor import extract_text_from_bytes
from hermes_gateway_client import HermesGatewayClient
from rag_context import RagContextManager


class GatewayChatPanel(QWidget):
    avatar_state_received = Signal(str)
    user_input_received = Signal(str)
    assistant_output_received = Signal(str)
    error_received = Signal(str)
    status_updated = Signal(str)
    assistant_delta_received = Signal(str)
    error_bubble_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.client = None
        self.client_lock = threading.Lock()
        self.rag = RagContextManager()
        self.messages = []
        self.current_assistant_bubble = None
        self.current_assistant_text = ""
        self.cancel_requested = False
        self.state_filter = StateOutputFilter()

        self.setObjectName("gatewayChatPanel")
        self.setStyleSheet(STYLE)
        self.status_updated.connect(self.set_status)
        self.assistant_delta_received.connect(self.append_assistant_text)
        self.error_bubble_received.connect(self.add_error_bubble)

        self.scroll_content = QWidget()
        self.messages_layout = QVBoxLayout()
        self.messages_layout.setContentsMargins(18, 18, 18, 18)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.scroll_content.setLayout(self.messages_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setWidget(self.scroll_content)

        self.attach_button = QPushButton("+")
        self.attach_button.setToolTip("Add files to RAG")
        self.attach_button.clicked.connect(self.attach_files)

        self.input = QTextEdit()
        self.input.setPlaceholderText("Message Hermes...")
        self.input.setFixedHeight(42)
        self.input.installEventFilter(self)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_generation)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self.send_message)

        composer = QHBoxLayout()
        composer.setContentsMargins(12, 10, 12, 6)
        composer.setSpacing(8)
        composer.addWidget(self.attach_button)
        composer.addWidget(self.input, stretch=1)
        composer.addWidget(self.stop_button)
        composer.addWidget(self.send_button)

        self.status_label = QLabel("Gateway chat mode")
        self.status_label.setObjectName("statusLabel")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area, stretch=1)
        layout.addLayout(composer)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        threading.Thread(target=self.ensure_gateway_ready, daemon=True).start()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    def ensure_gateway_ready(self):
        try:
            self.status_updated.emit("Starting Hermes Gateway...")
            if self.get_client().ensure_running():
                self.status_updated.emit("Hermes Gateway ready.")
            else:
                self.status_updated.emit("Gateway did not become ready. Use CLI fallback.")
                self.error_received.emit("Hermes Gateway did not become ready.")
        except Exception as e:
            self.status_updated.emit(f"Gateway unavailable: {e}")
            self.error_received.emit(str(e))

    @Slot()
    def send_message(self):
        message = self.input.toPlainText().strip()
        if not message:
            return

        self.input.clear()
        self.add_bubble(message, "user")
        self.user_input_received.emit(message)
        self.avatar_state_received.emit("thinking")
        self.set_status("Preparing message...")
        self.cancel_requested = False
        self.current_assistant_text = ""
        self.start_assistant_bubble()

        worker = threading.Thread(target=self._send_message_worker, args=(message,), daemon=True)
        worker.start()

    def _send_message_worker(self, message: str):
        try:
            client = self.get_client()
            if not client.ensure_running():
                raise RuntimeError("Hermes Gateway is not running.")

            prompt = self.rag.build_augmented_prompt(message)
            request_messages = self.messages + [{"role": "user", "content": prompt}]

            self.state_filter.reset()
            self.status_updated.emit("Hermes is thinking...")

            def on_delta(delta):
                if self.cancel_requested:
                    return
                clean_delta, states = self.state_filter.feed(delta)
                for state in states:
                    self.avatar_state_received.emit(state)
                if clean_delta:
                    self.current_assistant_text += clean_delta
                    self.avatar_state_received.emit("talking")
                    self.assistant_output_received.emit(clean_delta)
                    self.assistant_delta_received.emit(clean_delta)

            def on_tool_progress(text):
                self.status_updated.emit(text)
                lowered = (text or "").lower()
                if any(word in lowered for word in ["terminal", "code", "file"]):
                    self.avatar_state_received.emit("coding")
                else:
                    self.avatar_state_received.emit("searching")

            def on_done():
                clean_delta, states = self.state_filter.feed("", final=True)
                for state in states:
                    self.avatar_state_received.emit(state)
                if clean_delta:
                    self.current_assistant_text += clean_delta
                    self.avatar_state_received.emit("talking")
                    self.assistant_output_received.emit(clean_delta)
                    self.assistant_delta_received.emit(clean_delta)

            raw_answer = client.stream_chat(
                request_messages,
                on_text_delta=on_delta,
                on_tool_progress=on_tool_progress,
                on_done=on_done,
            )

            if self.cancel_requested:
                self.status_updated.emit("Stopped.")
                self.avatar_state_received.emit("idle")
                return

            answer = self.current_assistant_text.strip() or StateOutputFilter.clean_text(raw_answer).strip()
            self.messages.append({"role": "user", "content": message})
            self.messages.append({"role": "assistant", "content": answer})
            self.messages = self.messages[-20:]
            self.status_updated.emit("Done.")

        except Exception as e:
            self.status_updated.emit(f"Gateway error: {e}")
            self.error_received.emit(str(e))
            self.avatar_state_received.emit("warning")
            self.error_bubble_received.emit(f"Gateway error: {e}")

    @Slot()
    def stop_generation(self):
        self.cancel_requested = True
        self.set_status("Stop requested.")
        self.avatar_state_received.emit("idle")

    @Slot()
    def attach_files(self):
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
                source, text = extract_text_from_bytes(path, data)
                status = self.rag.add_file_text(source, text)
                self.set_status(status)
            except Exception as e:
                self.set_status(f"Could not index file: {e}")

    def reload_rag_config(self):
        self.rag.reload_config()
        self.set_status("RAG settings reloaded.")

    def start_assistant_bubble(self):
        self.current_assistant_bubble = self.add_bubble("", "assistant")

    def append_assistant_text(self, text: str):
        if not self.current_assistant_bubble:
            self.start_assistant_bubble()
        self.current_assistant_bubble.setText(self.current_assistant_bubble.text() + text)
        self.scroll_to_bottom()

    @Slot(str)
    def add_error_bubble(self, text: str):
        self.add_bubble(text, "error")

    def add_bubble(self, text: str, role: str):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        label.setMaximumWidth(720)
        label.setObjectName(f"{role}Bubble")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            row.addStretch(1)
            row.addWidget(label)
        else:
            row.addWidget(label)
            row.addStretch(1)

        insert_index = max(0, self.messages_layout.count() - 1)
        self.messages_layout.insertLayout(insert_index, row)
        self.scroll_to_bottom()
        return label

    def scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_status(self, text: str):
        self.status_label.setText(text or "")
        state = avatar_state_from_status(text)
        if state:
            self.avatar_state_received.emit(state)

    def stop(self):
        self.cancel_requested = True
        if self.client:
            self.client.stop_process()

    def get_client(self):
        with self.client_lock:
            if self.client is None:
                self.client = HermesGatewayClient()
            return self.client


class StateOutputFilter:
    event_re = re.compile(
        r"^[^\S\r\n]*@@S:(idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@[^\S\r\n]*(?:\r?\n)?"
        r"|@@S:(idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@",
        re.IGNORECASE | re.MULTILINE,
    )
    prose_re = re.compile(
        r"^[^\r\n]*(?:currentState|avatar\s*state|state|状态)[^\r\n]{0,40}?"
        r"(?:已更新为|更新为|变为|切换为|updated\s+to|set\s+to|changed\s+to)\s*"
        r"(idle|listening|thinking|searching|coding|explain|explaining|success|warning)"
        r"[^\r\n]*(?:\r?\n)?",
        re.IGNORECASE | re.MULTILINE,
    )

    def __init__(self):
        self.buffer = ""

    def reset(self):
        self.buffer = ""

    def feed(self, text: str, final: bool = False):
        data = self.buffer + (text or "")
        self.buffer = ""
        states = []

        data, found = self._remove_matches(data, self.event_re)
        states.extend(found)
        data, found = self._remove_matches(data, self.prose_re)
        states.extend(found)

        if not final:
            partial = find_partial_event_start(data)
            if partial >= 0:
                self.buffer = data[partial:]
                data = data[:partial]

        return data, states

    def _remove_matches(self, text: str, pattern):
        states = []

        def replace(match):
            for group in match.groups():
                if group:
                    states.append(normalize_state(group))
                    break
            return ""

        return pattern.sub(replace, text), states

    @classmethod
    def clean_text(cls, text: str) -> str:
        data, _ = cls().feed(text, final=True)
        return data


def normalize_state(state: str) -> str:
    state = (state or "").strip().lower()
    if state == "explaining":
        return "explain"
    return state or "idle"


def find_partial_event_start(text: str) -> int:
    marker_start = text.rfind("@@S:")
    if marker_start >= 0 and "@@" not in text[marker_start + 4 :]:
        return marker_start

    for prefix in ["@@S", "@@", "@"]:
        if text.endswith(prefix):
            return len(text) - len(prefix)

    return -1


def avatar_state_from_status(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""

    if any(word in lowered for word in ["error", "unavailable", "failed", "could not"]):
        return "warning"

    if any(word in lowered for word in ["stopped", "stop requested"]):
        return "idle"

    if "ready" in lowered:
        return "idle"

    if any(word in lowered for word in ["done", "indexed", "attached", "reloaded"]):
        return "success"

    if any(
        phrase in lowered
        for phrase in [
            "starting",
            "preparing",
            "sending",
            "thinking",
            "reading",
            "rag context",
        ]
    ):
        return "thinking"

    if any(word in lowered for word in ["terminal", "code", "indexing"]):
        return "coding"

    if any(word in lowered for word in ["tool", "search", "fetch", "request", "gateway"]):
        return "searching"

    return ""


STYLE = """
    QWidget#gatewayChatPanel {
        background-color: #f6f8fa;
    }

    QScrollArea {
        background-color: #f6f8fa;
        border: none;
    }

    QLabel#userBubble,
    QLabel#assistantBubble,
    QLabel#errorBubble {
        border-radius: 8px;
        padding: 10px 12px;
        font-size: 14px;
        line-height: 1.35;
        max-width: 720px;
    }

    QLabel#userBubble {
        background-color: #0969da;
        color: #ffffff;
    }

    QLabel#assistantBubble {
        background-color: #ffffff;
        color: #24292f;
        border: 1px solid #d0d7de;
    }

    QLabel#errorBubble {
        background-color: #fff1f1;
        color: #b42318;
        border: 1px solid #ffd0d0;
    }

    QTextEdit {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        padding: 8px 10px;
        background-color: #ffffff;
        color: #24292f;
        font-size: 14px;
    }

    QPushButton {
        min-width: 44px;
        height: 40px;
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
        color: #24292f;
        font-weight: 600;
    }

    QPushButton:hover {
        background-color: #eef2f6;
    }

    QPushButton#primaryButton {
        min-width: 76px;
        background-color: #0969da;
        border-color: #0969da;
        color: #ffffff;
    }

    QLabel#statusLabel {
        padding: 0 14px 10px 14px;
        color: #57606a;
        font-size: 12px;
    }
"""
