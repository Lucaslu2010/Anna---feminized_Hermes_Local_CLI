from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from rag_settings import load_rag_config, save_rag_config


PROVIDER_PRESETS = {
    "siliconflow": {
        "label": "SiliconFlow",
        "style": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-VL-Embedding-8B",
        "dimensions": "",
    },
    "openai": {
        "label": "OpenAI",
        "style": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
    },
    "anthropic": {
        "label": "Anthropic-style gateway",
        "style": "anthropic",
        "base_url": "",
        "model": "",
        "dimensions": 1024,
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "style": "openai",
        "base_url": "",
        "model": "",
        "dimensions": 1024,
    },
}


class RagSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Embedding settings")
        self.setModal(True)
        self.resize(520, 160)
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }

            QLabel {
                color: #24292f;
            }

            QLineEdit,
            QComboBox {
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
            QComboBox:focus {
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
        """)

        config = load_rag_config()

        self.provider_input = QComboBox()
        for value, preset in PROVIDER_PRESETS.items():
            self.provider_input.addItem(preset["label"], value)
        provider_index = self.provider_input.findData(
            config.get("embedding_provider", "siliconflow")
        )
        if provider_index >= 0:
            self.provider_input.setCurrentIndex(provider_index)
        self.provider_input.currentIndexChanged.connect(self.apply_provider_preset)

        self.base_url_input = QLineEdit()
        self.base_url_input.setText(config.get("embedding_base_url", ""))
        self.base_url_input.setPlaceholderText("https://api.example.com/v1")

        self.style_input = QComboBox()
        self.style_input.addItem("OpenAI-compatible", "openai")
        self.style_input.addItem("Anthropic-style gateway", "anthropic")
        style_index = self.style_input.findData(config.get("embedding_api_style", "openai"))
        if style_index >= 0:
            self.style_input.setCurrentIndex(style_index)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(config.get("embedding_api_key", ""))
        self.api_key_input.setPlaceholderText("Embedding API key")

        self.model_input = QLineEdit()
        self.model_input.setText(config.get("embedding_model", ""))

        self.dimensions_input = QLineEdit()
        self.dimensions_input.setText(str(config.get("embedding_dimensions", "")))
        self.dimensions_input.setAlignment(Qt.AlignRight)
        self.dimensions_input.setPlaceholderText("Optional")

        form = QFormLayout()
        form.addRow("Provider", self.provider_input)
        form.addRow("API Style", self.style_input)
        form.addRow("Base URL", self.base_url_input)
        form.addRow("API Key", self.api_key_input)
        form.addRow("Embedding Model", self.model_input)
        form.addRow("Dimensions", self.dimensions_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def apply_provider_preset(self):
        provider = self.provider_input.currentData()
        preset = PROVIDER_PRESETS.get(provider, {})

        if preset.get("base_url"):
            self.base_url_input.setText(preset["base_url"])
        if preset.get("model"):
            self.model_input.setText(preset["model"])
        self.dimensions_input.setText(str(preset.get("dimensions", "")))

        style_index = self.style_input.findData(preset.get("style", "openai"))
        if style_index >= 0:
            self.style_input.setCurrentIndex(style_index)

    def save_and_accept(self):
        dimensions_text = self.dimensions_input.text().strip()
        try:
            dimensions = int(dimensions_text) if dimensions_text else ""
        except ValueError:
            QMessageBox.warning(self, "Invalid Dimensions", "Dimensions must be a number.")
            return

        save_rag_config(
            {
                "embedding_provider": self.provider_input.currentData() or "custom",
                "embedding_api_style": self.style_input.currentData() or "openai",
                "embedding_api_key": self.api_key_input.text().strip(),
                "embedding_base_url": self.base_url_input.text().strip(),
                "embedding_model": self.model_input.text().strip(),
                "embedding_dimensions": dimensions,
            }
        )
        self.accept()
