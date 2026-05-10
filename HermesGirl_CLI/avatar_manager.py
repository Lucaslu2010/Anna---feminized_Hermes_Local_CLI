import os
import re
from typing import Dict, Optional
from paths import resource_path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap,QImage
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QGridLayout,
    QVBoxLayout,
    QWidget,
)


class AvatarManager:
    """
    Manage avatar states and map Hermes/user events to avatar images.

    Expected image files:
        assets/avatar/idle.png
        assets/avatar/listening.png
        assets/avatar/thinking.png
        assets/avatar/talking.png
        assets/avatar/explain.png
        assets/avatar/coding.png
        assets/avatar/searching.png
        assets/avatar/happy.png
        assets/avatar/warning.png
        assets/avatar/success.png
        assets/avatar/sleep.png

    confused.png is optional. If missing, it will fall back to thinking.png.
    """

    DEFAULT_STATES = [
        "idle",
        "listening",
        "thinking",
        "talking",
        "explain",
        "coding",
        "searching",
        "happy",
        "warning",
        "success",
        "sleep",
        "confused",
    ]

    def __init__(self, avatar_dir: str = None):
        self.avatar_dir = avatar_dir or resource_path("assets/avatar")
        self.current_state = "idle"
        self.pixmaps: Dict[str, QPixmap] = {}

        self._load_images()

    def _load_images(self):
        for state in self.DEFAULT_STATES:
            path = os.path.join(self.avatar_dir, f"{state}.png")

            if os.path.exists(path):
                pixmap = QPixmap(path)

                if not pixmap.isNull():
                    self.pixmaps[state] = pixmap

        self._load_alias("explain", "explaining")

        # Fallbacks
        if "confused" not in self.pixmaps:
            if "thinking" in self.pixmaps:
                self.pixmaps["confused"] = self.pixmaps["thinking"]
            elif "idle" in self.pixmaps:
                self.pixmaps["confused"] = self.pixmaps["idle"]

        for state in self.DEFAULT_STATES:
            if state not in self.pixmaps and "idle" in self.pixmaps:
                self.pixmaps[state] = self.pixmaps["idle"]

    def _load_alias(self, state: str, filename_stem: str):
        if state in self.pixmaps:
            return

        path = os.path.join(self.avatar_dir, f"{filename_stem}.png")
        if not os.path.exists(path):
            return

        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.pixmaps[state] = pixmap

    def has_state(self, state: str) -> bool:
        return state in self.pixmaps

    def get_pixmap(self, state: Optional[str] = None) -> Optional[QPixmap]:
        state = state or self.current_state

        if state in self.pixmaps:
            return self.pixmaps[state]

        return self.pixmaps.get("idle")

    def set_state(self, state: str):
        if state not in self.DEFAULT_STATES:
            state = "idle"

        if state not in self.pixmaps:
            state = "idle"

        self.current_state = state

    def get_state(self) -> str:
        return self.current_state



    def on_user_input_started(self) -> str:
        self.set_state("listening")
        return self.current_state

    def on_user_message_sent(self, text: str) -> str:
        self.set_state("listening")
        return self.current_state

    def on_loading(self) -> str:
        self.set_state("thinking")
        return self.current_state

    def on_response_finished(self) -> str:
        self.set_state("idle")
        return self.current_state

    def on_sleep_timeout(self) -> str:
        self.set_state("sleep")
        return self.current_state

    def classify_output(self, text: str) -> str:
        """
        Classify Hermes visible output into avatar state.

        Priority:
            warning > success > coding > searching > confused > explain > happy > talking
        """

        if not text:
            return "talking"

        lower = text.lower()

        if self._contains_warning_signal(lower):
            return "warning"

        if self._contains_success_signal(lower):
            return "success"

        if self._contains_coding_signal(text, lower):
            return "coding"

        if self._contains_searching_signal(lower):
            return "searching"

        if self._contains_confused_signal(lower):
            return "confused"

        if self._contains_explain_signal(lower):
            return "explain"

        if self._contains_happy_signal(lower):
            return "happy"

        return "talking"

    def on_agent_output(self, text: str) -> str:
        state = self.classify_output(text)
        self.set_state(state)
        return self.current_state

    @staticmethod
    def _contains_warning_signal(lower: str) -> bool:
        keywords = [
            "error",
            "failed",
            "failure",
            "warning",
            "dangerous",
            "permission denied",
            "denied",
            "not found",
            "cannot",
            "can't",
            "unable",
            "http 500",
            "internal server error",
            "api call failed",
            "exception",
            "traceback",
            "crash",
            "invalid",
            "timeout",
            "timed out",
            "refused",
            "forbidden",
            "unauthorized",
            "ssl",
            "certificate",
            "报错",
            "错误",
            "失败",
            "无法",
            "不能",
            "危险",
            "权限",
            "崩溃",
        ]

        return any(keyword in lower for keyword in keywords)

    @staticmethod
    def _contains_success_signal(lower: str) -> bool:
        keywords = [
            "completed",
            "finished",
            "success",
            "successful",
            "created",
            "saved",
            "updated",
            "done",
            "fixed",
            "resolved",
            "generated",
            "完成",
            "已完成",
            "成功",
            "保存好了",
            "生成好了",
            "修改好了",
            "修好了",
            "解决了",
        ]

        return any(keyword in lower for keyword in keywords)

    @staticmethod
    def _contains_coding_signal(original: str, lower: str) -> bool:
        if "```" in original:
            return True

        patterns = [
            r"\bimport\s+\w+",
            r"\bfrom\s+\w+\s+import\b",
            r"\bdef\s+\w+\s*\(",
            r"\bclass\s+\w+",
            r"\bsubprocess\b",
            r"\bthreading\b",
            r"\bqueue\b",
            r"\bqthread\b",
            r"\bqtimer\b",
            r"\bfunction\b",
            r"\bvariable\b",
            r"\bsyntax\b",
            r"\bdebug\b",
            r"\bbug\b",
            r"\btraceback\b",
            r"\bexception\b",
            r"\bpython\b",
            r"\bjavascript\b",
            r"\bhtml\b",
            r"\bcss\b",
            r"\bsql\b",
            r"\bjson\b",
            r"\bapi\b",
            r"\bcode\b",
        ]

        if any(re.search(pattern, lower) for pattern in patterns):
            return True

        chinese_keywords = [
            "代码",
            "函数",
            "变量",
            "类",
            "调试",
            "bug",
            "脚本",
            "程序",
            "报错",
        ]

        return any(keyword in lower for keyword in chinese_keywords)

    @staticmethod
    def _contains_searching_signal(lower: str) -> bool:
        keywords = [
            "curl",
            "get ",
            "post ",
            "fetch",
            "search",
            "request",
            "endpoint",
            "openrouter",
            "browser",
            "website",
            "web",
            "tool",
            "command",
            "terminal",
            "shell",
            "http://",
            "https://",
            "查询",
            "搜索",
            "请求",
            "网站",
            "网页",
            "命令",
            "终端",
            "工具",
        ]

        return any(keyword in lower for keyword in keywords)

    @staticmethod
    def _contains_confused_signal(lower: str) -> bool:
        keywords = [
            "not sure",
            "unclear",
            "could you clarify",
            "what do you mean",
            "which one",
            "need more information",
            "i need more",
            "ambiguous",
            "不确定",
            "不清楚",
            "没看懂",
            "需要你提供",
            "请确认",
            "你是指",
            "具体是",
        ]

        return any(keyword in lower for keyword in keywords)

    @staticmethod
    def _contains_explain_signal(lower: str) -> bool:
        keywords = [
            "first",
            "second",
            "third",
            "finally",
            "step",
            "solution",
            "because",
            "therefore",
            "for example",
            "in short",
            "the reason",
            "you need to",
            "you should",
            "recommend",
            "suggest",
            "首先",
            "其次",
            "然后",
            "最后",
            "步骤",
            "方案",
            "建议",
            "原因",
            "具体来说",
            "也就是说",
            "换句话说",
        ]

        return any(keyword in lower for keyword in keywords)

    @staticmethod
    def _contains_happy_signal(lower: str) -> bool:
        # Keep this conservative, otherwise many normal messages will become happy.
        short_text = len(lower.strip()) <= 80

        keywords = [
            "ok",
            "okay",
            "sure",
            "great",
            "nice",
            "glad",
            "no problem",
            "没问题",
            "好的",
            "可以",
            "好滴",
            "行",
        ]

        return short_text and any(keyword in lower for keyword in keywords)


class AvatarPanel(QFrame):
    """
    Avatar display panel.

    It displays:
        1. Avatar image
        2. Debug state label under the avatar
    """

    def __init__(self, avatar_manager: Optional[AvatarManager] = None):
        super().__init__()

        self.avatar_manager = avatar_manager or AvatarManager()
        self.cropped_pixmap_cache = {}
        self.setObjectName("avatarPanel")
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.setStyleSheet("""
            QFrame#avatarPanel {
                background-color: #ffffff;
                border-left: 1px solid rgba(230, 230, 230, 180);
            }
        """)

        self.button_bar = QWidget()
        self.button_bar.setObjectName("avatarButtonBar")
        self.button_bar.setStyleSheet("""
            QWidget#avatarButtonBar {
                background: transparent;
            }

            QPushButton {
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 8px;
                padding: 5px 6px;
                color: #24292f;
                font-size: 10px;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #eef2f6;
                border-color: #afb8c1;
            }

            QPushButton:pressed {
                background-color: #dbeafe;
                border-color: #0969da;
            }
        """)

        self.button_layout = QGridLayout()
        self.button_layout.setContentsMargins(0, 0, 0, 10)
        self.button_layout.setSpacing(6)
        self.button_bar.setLayout(self.button_layout)
        self.button_count = 0

        self.avatar_card = QFrame()
        self.avatar_card.setObjectName("avatarCard")
        self.avatar_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.avatar_card.setStyleSheet("""
            QFrame#avatarCard {
                background-color: rgba(204, 204, 204, 150);
                border-radius: 28px;
                border: none;
            }
        """)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setMinimumSize(QSize(260, 420))
        self.image_label.setStyleSheet("""
            QLabel {
                background: transparent;
            }
        """)

        self.state_label = QLabel("State: idle")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 14px;
                font-weight: 500;
                background: transparent;
                padding-top: 8px;
                padding-bottom: 4px;
            }
        """)

        self.debug_label = QLabel("Avatar debug enabled")
        self.debug_label.setAlignment(Qt.AlignCenter)
        self.debug_label.setStyleSheet("""
            QLabel {
                color: #aaaaaa;
                font-size: 12px;
                background: transparent;
                padding-bottom: 8px;
            }
        """)

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(8)
        card_layout.addWidget(self.image_label, stretch=1)
        card_layout.addWidget(self.state_label)
        card_layout.addWidget(self.debug_label)

        self.avatar_card.setLayout(card_layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 24)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.button_bar)
        main_layout.addWidget(self.avatar_card)

        self.setLayout(main_layout)

        self.update_avatar("idle")

    def add_panel_button(self, text: str, callback):
        button = QPushButton(text)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setMinimumHeight(28)
        button.clicked.connect(callback)

        row = self.button_count // 2
        column = self.button_count % 2
        self.button_layout.addWidget(button, row, column)
        self.button_count += 1

        return button

    def update_avatar(self, state: str):
        self.avatar_manager.set_state(state)
        self._refresh_image()
        self._refresh_state_text()

    def _crop_transparent_edges(self, pixmap: QPixmap) -> QPixmap:
        """
        Crop transparent empty area around the avatar.
        This makes the character appear larger when displayed.
        """

        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)

        width = image.width()
        height = image.height()

        min_x = width
        min_y = height
        max_x = -1
        max_y = -1

        alpha_threshold = 10

        for y in range(height):
            for x in range(width):
                alpha = image.pixelColor(x, y).alpha()

                if alpha > alpha_threshold:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

        if max_x < min_x or max_y < min_y:
            return pixmap

        padding = 20

        min_x = max(0, min_x - padding)
        min_y = max(0, min_y - padding)
        max_x = min(width - 1, max_x + padding)
        max_y = min(height - 1, max_y + padding)

        cropped = image.copy(
            min_x,
            min_y,
            max_x - min_x + 1,
            max_y - min_y + 1,
        )

        return QPixmap.fromImage(cropped)

    def update_by_user_message(self, text: str):
        state = self.avatar_manager.on_user_message_sent(text)
        self.update_avatar(state)

    def update_by_loading(self):
        state = self.avatar_manager.on_loading()
        self.update_avatar(state)

    def update_by_agent_output(self, text: str):
        state = self.avatar_manager.on_agent_output(text)
        self.update_avatar(state)

    def update_to_idle(self):
        state = self.avatar_manager.on_response_finished()
        self.update_avatar(state)

    def update_to_sleep(self):
        state = self.avatar_manager.on_sleep_timeout()
        self.update_avatar(state)

    def _refresh_state_text(self):
        state = self.avatar_manager.get_state()
        self.state_label.setText(f"State: {state}")

    def _refresh_image(self):
        pixmap = self.avatar_manager.get_pixmap()

        if pixmap is None or pixmap.isNull():
            self.image_label.setText("Avatar image not found")
            return

        current_state = self.avatar_manager.get_state()

        # Crop only once for each state, then reuse cached cropped pixmap.
        if current_state not in self.cropped_pixmap_cache:
            self.cropped_pixmap_cache[current_state] = self._crop_transparent_edges(pixmap)

        pixmap = self.cropped_pixmap_cache[current_state]

        target_size = self.image_label.size()

        if target_size.width() <= 10 or target_size.height() <= 10:
            target_size = QSize(360, 620)

        scale_factor = 1.25

        target_width = int(target_size.width() * scale_factor)
        target_height = int(target_size.height() * scale_factor)

        scaled = pixmap.scaled(
            QSize(target_width, target_height),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_image()
