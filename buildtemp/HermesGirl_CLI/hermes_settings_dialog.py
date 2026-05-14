from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from hermes_locator import describe_hermes_paths
from hermes_settings import (
    load_hermes_config,
    read_hermes_memory,
    read_hermes_skills_text,
    save_hermes_config,
)

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
"""


class HermesSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Hermes Settings")
        self.setModal(True)
        self.resize(680, 260)
        self.setStyleSheet(DIALOG_STYLE)

        config = load_hermes_config()
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

        buttons = QDialogButtonBox(QDialogButtonBox.Save)
        buttons.accepted.connect(self.save_config)

        layout = QVBoxLayout()
        layout.addWidget(self.config_path_label)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setLayout(layout)

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
        paths = describe_hermes_paths(create=True)
        self.config_path_label.setText(
            f"Hermes: {paths['executable']}\n"
            f"Config: {path}\n"
            f"Secrets: {paths['env']}"
        )
        QMessageBox.information(
            self,
            "Hermes Settings Saved",
            "Hermes settings were saved. Restart Hermes if the running session does not pick them up.",
        )


class HermesMemoryDialog(QDialog):
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

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.memory_text)
        self.setLayout(layout)

        self.refresh_memory()

    def refresh_memory(self):
        self.memory_text.setPlainText(read_hermes_memory())


class HermesSkillsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Hermes Skills")
        self.setModal(False)
        self.resize(860, 600)
        self.setStyleSheet(DIALOG_STYLE)

        self.skills_text = QTextEdit()
        self.skills_text.setReadOnly(True)
        self.skills_text.setLineWrapMode(QTextEdit.NoWrap)

        refresh_button = QPushButton("Refresh Skills")
        refresh_button.clicked.connect(self.refresh_skills)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.skills_text)
        self.setLayout(layout)

        self.refresh_skills()

    def refresh_skills(self):
        self.skills_text.setPlainText(read_hermes_skills_text())
