import html
import os
import re
import threading

from PySide6.QtCore import QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
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
from file_request_dialog import MissingFileDialog
from hermes_gateway_client import HermesGatewayClient
from rag_context import RagContextManager
from rag_files import copy_file_to_rag_storage
from upload_popup import DraggableUploadButton, UploadPopup
from web_agent_client import WebAgentClient
from web_file_registry import (
    get_file_record,
    mark_forgotten,
    mark_uploaded,
    remember_local_file,
)
from web_settings import is_web_mode_enabled, load_web_config


class ChatBubble(QLabel):
    def __init__(self, text: str = ""):
        super().__init__()
        self.raw_text = ""
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        self.set_raw_text(text)

    def set_raw_text(self, text: str):
        self.raw_text = text or ""
        self.setText(render_chat_markdown(self.raw_text))
        self.adjustSize()

    def append_raw_text(self, text: str):
        self.set_raw_text(self.raw_text + (text or ""))


def render_chat_markdown(text: str) -> str:
    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*([^*\n][^*\n]*(?:\*[^*\n]+)*)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return f'<div style="white-space: normal;">{escaped}</div>'


class GatewayChatPanel(QWidget):
    avatar_state_received = Signal(str)
    user_input_received = Signal(str)
    assistant_output_received = Signal(str)
    error_received = Signal(str)
    status_updated = Signal(str)
    assistant_delta_received = Signal(str)
    error_bubble_received = Signal(str)
    upload_progress_received = Signal(str, str, int, str)
    file_reupload_requested = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.client = None
        self.client_mode = ""
        self.client_lock = threading.Lock()
        self.rag = None if is_web_mode_enabled() else RagContextManager()
        self.messages = []
        self.current_assistant_bubble = None
        self.current_assistant_text = ""
        self.cancel_requested = False
        self.state_filter = StateOutputFilter()
        self.pending_file_requests = {}

        self.setObjectName("gatewayChatPanel")
        self.setStyleSheet(STYLE)
        self.status_updated.connect(self.set_status)
        self.assistant_delta_received.connect(self.append_assistant_text)
        self.error_bubble_received.connect(self.add_error_bubble)
        self.upload_progress_received.connect(self.handle_upload_progress)
        self.file_reupload_requested.connect(self.handle_file_reupload_dialog)

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

        self.upload_popup = UploadPopup(self)
        self.upload_popup.hide()
        self.upload_popup.minimize_requested.connect(self.minimize_upload_popup)
        self.upload_popup.dragged.connect(self.mark_upload_popup_dragged)
        self.upload_popup_user_positioned = False
        self.upload_toggle_user_positioned = False
        self.upload_animation = None

        self.upload_toggle_button = DraggableUploadButton("Uploads", self)
        self.upload_toggle_button.setObjectName("uploadToggleButton")
        self.upload_toggle_button.setStyleSheet("""
            QPushButton#uploadToggleButton {
                border: 1px solid #d0d7de;
                border-radius: 16px;
                padding: 6px 12px;
                background-color: #ffffff;
                color: #24292f;
                font-weight: 700;
            }
        """)
        self.upload_toggle_button.clicked.connect(self.show_upload_popup)
        self.upload_toggle_button.dragged.connect(self.mark_upload_toggle_dragged)
        self.upload_toggle_button.hide()
        self.position_upload_widgets()

        threading.Thread(target=self.ensure_gateway_ready, daemon=True).start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_upload_widgets()

    def position_upload_widgets(self):
        if not hasattr(self, "upload_popup") or not hasattr(self, "upload_toggle_button"):
            return
        popup_height = min(250, max(170, self.height() // 3))
        self.upload_popup.setFixedHeight(popup_height)
        if self.upload_popup_user_positioned:
            self.upload_popup.move(self.clamp_upload_child_pos(self.upload_popup, self.upload_popup.pos()))
        else:
            self.upload_popup.move(self.default_upload_popup_pos())

        self.upload_toggle_button.adjustSize()
        if self.upload_toggle_user_positioned:
            self.upload_toggle_button.move(
                self.clamp_upload_child_pos(self.upload_toggle_button, self.upload_toggle_button.pos())
            )
        else:
            self.upload_toggle_button.move(self.default_upload_toggle_pos())

    def default_upload_popup_pos(self):
        margin = 18
        return QPoint(
            max(margin, self.width() - self.upload_popup.width() - margin),
            max(margin, self.height() - self.upload_popup.height() - 58),
        )

    def default_upload_toggle_pos(self):
        margin = 18
        return QPoint(
            max(margin, self.width() - self.upload_toggle_button.width() - margin),
            max(margin, self.height() - self.upload_toggle_button.height() - 58),
        )

    def clamp_upload_child_pos(self, child, pos: QPoint):
        margin = 8
        max_x = max(margin, self.width() - child.width() - margin)
        max_y = max(margin, self.height() - child.height() - margin)
        return QPoint(
            min(max(pos.x(), margin), max_x),
            min(max(pos.y(), margin), max_y),
        )

    @Slot()
    def mark_upload_popup_dragged(self):
        self.upload_popup_user_positioned = True

    @Slot()
    def mark_upload_toggle_dragged(self):
        self.upload_toggle_user_positioned = True

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    def ensure_gateway_ready(self):
        try:
            if self.is_web_mode():
                self.status_updated.emit("Connecting to web Hermes server...")
                status = self.get_client().health_status()
                if not status.get("ok"):
                    message = (
                        f"Web Hermes server is unavailable at {status.get('server_url', '')}. "
                        f"{status.get('error', '')}"
                    ).strip()
                    self.status_updated.emit(message)
                    self.error_received.emit(message)
                    return

                if status.get("hermes_found") is False:
                    self.status_updated.emit("Web server ready, but Hermes is not installed or not on PATH on the server.")
                elif status.get("gateway_ready"):
                    self.status_updated.emit("Web Hermes server and gateway ready.")
                else:
                    self.status_updated.emit("Web server ready. Starting Hermes gateway...")
                    warmup = self.get_client().start_gateway()
                    if warmup.get("ok") and warmup.get("gateway_ready"):
                        self.status_updated.emit("Web Hermes server and gateway ready.")
                    else:
                        message = (
                            "Web server is reachable, but Hermes gateway did not start. "
                            f"{warmup.get('error') or warmup.get('gateway_error') or ''} "
                            f"{'Log: ' + warmup.get('gateway_log') if warmup.get('gateway_log') else ''}"
                        ).strip()
                        self.status_updated.emit(message)
                        self.error_received.emit(message)
                return

            self.status_updated.emit("Starting Hermes Gateway...")
            if self.get_client().ensure_running():
                self.status_updated.emit("Hermes Gateway ready.")
            else:
                self.status_updated.emit("Gateway did not become ready.")
                self.error_received.emit("Hermes Gateway did not become ready.")
        except Exception as e:
            self.status_updated.emit(f"Gateway unavailable: {e}")
            self.error_received.emit(str(e))

    def reload_connection_mode(self):
        with self.client_lock:
            if isinstance(self.client, HermesGatewayClient):
                self.client.stop_process()
            self.client = None
            self.client_mode = ""
        if self.is_web_mode():
            self.rag = None
        threading.Thread(target=self.ensure_gateway_ready, daemon=True).start()

    def is_web_mode(self) -> bool:
        return is_web_mode_enabled()

    def get_rag_manager(self) -> RagContextManager:
        if self.rag is None:
            self.rag = RagContextManager()
        return self.rag

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
            for attempt in range(4):
                client = self.get_client()
                if not client.ensure_running():
                    if self.is_web_mode():
                        status = client.health_status()
                        raise RuntimeError(
                            f"Web Hermes server is not reachable at {status.get('server_url', '')}. "
                            f"{status.get('error', '')}"
                        )
                    raise RuntimeError("Local Hermes Gateway is not running.")

                if self.is_web_mode():
                    request_messages = self.messages + [{"role": "user", "content": message}]
                else:
                    prompt = self.get_rag_manager().build_augmented_prompt(message)
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
                    if any(word in lowered for word in ["terminal", "code", "file", "upload"]):
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

                if self.is_web_mode():
                    result = client.stream_chat(
                        request_messages,
                        on_text_delta=on_delta,
                        on_tool_progress=on_tool_progress,
                        on_done=on_done,
                        on_file_request=self.resolve_requested_file,
                    )
                    raw_answer = result.text
                    if result.file_request_handled and attempt < 3 and not self.cancel_requested:
                        self.current_assistant_text = ""
                        self.status_updated.emit("File restored. Asking Hermes again...")
                        continue
                else:
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
                return

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

        if self.is_web_mode():
            for path in paths:
                self.upload_file_to_server(path)
            return

        for path in paths:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                _, text = extract_text_from_bytes(path, data)
                source = copy_file_to_rag_storage(path)
                status = self.get_rag_manager().add_file_text(source, text)
                self.set_status(status)
            except Exception as e:
                self.set_status(f"Could not index file: {e}")

    def reload_rag_config(self):
        self.get_rag_manager().reload_config()
        self.set_status("RAG settings reloaded.")

    def upload_file_to_server(self, path: str, key: str = ""):
        web_config = load_web_config()
        record = remember_local_file(
            path,
            key=key or "",
            server_url=web_config.get("server_url", ""),
            username=web_config.get("username", ""),
        )
        key = record.get("key", "")
        filename = record.get("filename") or os.path.basename(path)
        self.show_upload_popup()
        self.upload_progress_received.emit(key, filename, 0, "Queued")

        worker = threading.Thread(
            target=self._upload_file_worker,
            args=(path, key, filename),
            daemon=True,
        )
        worker.start()

    def _upload_file_worker(self, path: str, key: str, filename: str) -> bool:
        try:
            client = self.get_client()
            self.status_updated.emit(f"Uploading {filename}...")

            def on_progress(sent, total):
                percent = int((sent / max(total, 1)) * 100)
                self.upload_progress_received.emit(key, filename, percent, "Uploading")

            result = client.upload_file(path, key, on_progress=on_progress)
            server_file = result.get("file", {}) if isinstance(result, dict) else {}
            web_config = load_web_config()
            mark_uploaded(
                key,
                filename=filename,
                server_url=web_config.get("server_url", ""),
                username=web_config.get("username", ""),
            )
            final_status = upload_result_status(server_file)
            self.upload_progress_received.emit(key, filename, 100, final_status)
            self.status_updated.emit(f"Uploaded {filename}. {final_status}")
            return True
        except Exception as e:
            self.upload_progress_received.emit(key, filename, 0, f"Failed: {e}")
            self.status_updated.emit(f"Could not upload {filename}: {e}")
            return False

    def resolve_requested_file(self, payload: dict) -> bool:
        key = str(payload.get("key", "") or "")
        filename = str(payload.get("filename", "") or "requested file")
        if not key:
            return False

        record = get_file_record(key)
        path = record.get("local_path", "")
        if path and os.path.isfile(path):
            return self._upload_file_worker(path, key, filename or os.path.basename(path))

        request_id = f"{key}:{threading.get_ident()}"
        pending = {"event": threading.Event(), "choice": "", "path": ""}
        self.pending_file_requests[request_id] = pending
        self.file_reupload_requested.emit(request_id, key, filename)
        pending["event"].wait()
        self.pending_file_requests.pop(request_id, None)

        if pending.get("choice") == MissingFileDialog.CHOOSE_FILE and pending.get("path"):
            remember_local_file(
                pending["path"],
                key=key,
                server_url=load_web_config().get("server_url", ""),
                username=load_web_config().get("username", ""),
            )
            return self._upload_file_worker(
                pending["path"],
                key,
                os.path.basename(pending["path"]),
            )

        if pending.get("choice") == MissingFileDialog.FORGET_FILE:
            try:
                self.get_client().forget_file(key)
                mark_forgotten(key)
                self.status_updated.emit(f"Hermes will stop using {filename}.")
            except Exception as e:
                self.status_updated.emit(f"Could not update server file choice: {e}")
        return False

    @Slot(str, str, str)
    def handle_file_reupload_dialog(self, request_id: str, key: str, filename: str):
        pending = self.pending_file_requests.get(request_id)
        if pending is None:
            return

        dialog = MissingFileDialog(filename, self)
        dialog.exec()
        pending["choice"] = dialog.choice or MissingFileDialog.FORGET_FILE
        pending["path"] = dialog.selected_path
        pending["event"].set()

    @Slot(str, str, int, str)
    def handle_upload_progress(self, upload_id: str, filename: str, percent: int, status: str):
        self.upload_popup.update_upload(upload_id, filename, percent, status)
        if not self.upload_popup.isVisible() and not self.upload_toggle_button.isVisible():
            self.show_upload_popup()

    @Slot()
    def minimize_upload_popup(self):
        if not self.upload_popup.isVisible():
            return

        self.stop_upload_animation()
        self.upload_toggle_button.adjustSize()

        if self.upload_toggle_user_positioned:
            target_pos = self.clamp_upload_child_pos(
                self.upload_toggle_button,
                self.upload_toggle_button.pos(),
            )
        elif self.upload_popup_user_positioned:
            target_pos = self.clamp_upload_child_pos(
                self.upload_toggle_button,
                self.upload_popup.pos(),
            )
            self.upload_toggle_user_positioned = True
        else:
            target_pos = self.default_upload_toggle_pos()

        self.upload_toggle_button.move(target_pos)
        self.upload_toggle_button.show()
        self.upload_toggle_button.raise_()

        popup_effect = self.upload_opacity_effect(self.upload_popup)
        toggle_effect = self.upload_opacity_effect(self.upload_toggle_button)
        popup_effect.setOpacity(1.0)
        toggle_effect.setOpacity(0.0)

        group = QParallelAnimationGroup(self)
        self.add_pos_animation(group, self.upload_popup, self.upload_popup.pos(), target_pos)
        self.add_opacity_animation(group, popup_effect, 1.0, 0.0)
        self.add_opacity_animation(group, toggle_effect, 0.0, 1.0)

        def finish():
            self.upload_popup.hide()
            popup_effect.setOpacity(1.0)
            toggle_effect.setOpacity(1.0)
            self.upload_toggle_button.raise_()
            self.upload_animation = None

        group.finished.connect(finish)
        self.upload_animation = group
        group.start()

    @Slot()
    def show_upload_popup(self):
        self.stop_upload_animation()

        self.upload_popup.setFixedHeight(min(250, max(170, self.height() // 3)))
        if self.upload_toggle_button.isVisible():
            target_pos = self.clamp_upload_child_pos(
                self.upload_popup,
                self.upload_toggle_button.pos(),
            )
            if self.upload_toggle_user_positioned:
                self.upload_popup_user_positioned = True
            start_pos = self.clamp_upload_child_pos(self.upload_popup, target_pos + QPoint(0, 18))
        elif self.upload_popup_user_positioned:
            target_pos = self.clamp_upload_child_pos(self.upload_popup, self.upload_popup.pos())
            start_pos = self.clamp_upload_child_pos(self.upload_popup, target_pos + QPoint(0, 18))
        else:
            target_pos = self.default_upload_popup_pos()
            start_pos = self.clamp_upload_child_pos(self.upload_popup, target_pos + QPoint(0, 18))

        self.upload_popup.move(start_pos)
        self.upload_popup.show()
        self.upload_popup.raise_()

        popup_effect = self.upload_opacity_effect(self.upload_popup)
        toggle_effect = self.upload_opacity_effect(self.upload_toggle_button)
        popup_effect.setOpacity(0.0)
        if self.upload_toggle_button.isVisible():
            toggle_effect.setOpacity(1.0)

        group = QParallelAnimationGroup(self)
        self.add_pos_animation(group, self.upload_popup, start_pos, target_pos)
        self.add_opacity_animation(group, popup_effect, 0.0, 1.0)
        if self.upload_toggle_button.isVisible():
            self.add_opacity_animation(group, toggle_effect, 1.0, 0.0)

        def finish():
            self.upload_toggle_button.hide()
            popup_effect.setOpacity(1.0)
            toggle_effect.setOpacity(1.0)
            self.upload_popup.move(target_pos)
            self.upload_popup.raise_()
            self.upload_animation = None

        group.finished.connect(finish)
        self.upload_animation = group
        group.start()

    def stop_upload_animation(self):
        if self.upload_animation is not None:
            self.upload_animation.stop()
            self.upload_animation = None

    def upload_opacity_effect(self, widget):
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        return effect

    def add_pos_animation(self, group, widget, start_pos: QPoint, end_pos: QPoint):
        animation = QPropertyAnimation(widget, b"pos", group)
        animation.setDuration(180)
        animation.setStartValue(start_pos)
        animation.setEndValue(end_pos)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(animation)

    def add_opacity_animation(self, group, effect, start_value: float, end_value: float):
        animation = QPropertyAnimation(effect, b"opacity", group)
        animation.setDuration(180)
        animation.setStartValue(start_value)
        animation.setEndValue(end_value)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(animation)

    def start_assistant_bubble(self):
        self.current_assistant_bubble = self.add_bubble("", "assistant")

    def append_assistant_text(self, text: str):
        if not self.current_assistant_bubble:
            self.start_assistant_bubble()
        self.current_assistant_bubble.append_raw_text(text)
        self.scroll_to_bottom()

    @Slot(str)
    def add_error_bubble(self, text: str):
        self.add_bubble(text, "error")

    def add_bubble(self, text: str, role: str):
        label = ChatBubble(text)
        label.setMaximumWidth(720)
        label.setObjectName(f"{role}Bubble")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignTop)
        if role == "user":
            row.addStretch(1)
            row.addWidget(label, 0, Qt.AlignTop)
        else:
            row.addWidget(label, 0, Qt.AlignTop)
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
        if isinstance(self.client, HermesGatewayClient):
            self.client.stop_process()

    def get_client(self):
        requested_mode = "web" if self.is_web_mode() else "local"
        with self.client_lock:
            if self.client is not None and self.client_mode != requested_mode:
                if isinstance(self.client, HermesGatewayClient):
                    self.client.stop_process()
                self.client = None
                self.client_mode = ""

            if self.client is None:
                self.client = WebAgentClient() if requested_mode == "web" else HermesGatewayClient()
                self.client_mode = requested_mode
            return self.client


def upload_result_status(server_file: dict) -> str:
    index_status = str((server_file or {}).get("index_status", "") or "")
    if (server_file or {}).get("rag_indexed"):
        return index_status or "Indexed into server RAG."

    lowered = index_status.lower()
    if "embedding api key" in lowered or "enable vector search" in lowered:
        return "Stored on server. Server embedding API key is missing, so Original RAG was not indexed."
    if "no readable" in lowered:
        return "Stored on server. No readable text was found for Original RAG."
    if index_status:
        return f"Stored on server. {index_status}"
    return "Stored on server. Original RAG was not indexed."


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

    if any(word in lowered for word in ["done", "indexed", "attached", "reloaded", "uploaded"]):
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
            "uploading",
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
