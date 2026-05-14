import json
import os
from typing import Dict


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".anna_rag")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


DEFAULT_CONFIG = {
    "embedding_provider": "siliconflow",
    "embedding_api_style": "openai",
    "embedding_api_key": "",
    "embedding_base_url": "https://api.siliconflow.cn/v1",
    "embedding_model": "Qwen/Qwen3-VL-Embedding-8B",
    "embedding_dimensions": "",
}


def load_rag_config() -> Dict:
    config = dict(DEFAULT_CONFIG)

    if not os.path.exists(CONFIG_PATH):
        return config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return config

    if isinstance(saved, dict):
        config.update(saved)

    config = migrate_legacy_config(config)
    return config


def migrate_legacy_config(config: Dict) -> Dict:
    if not config.get("embedding_api_key") and config.get("siliconflow_api_key"):
        config["embedding_api_key"] = config.get("siliconflow_api_key", "")

    if not config.get("embedding_model") and config.get("siliconflow_embedding_model"):
        config["embedding_model"] = config.get("siliconflow_embedding_model", "")

    if (
        not config.get("embedding_dimensions")
        and config.get("siliconflow_embedding_dimensions")
    ):
        config["embedding_dimensions"] = config.get("siliconflow_embedding_dimensions")

    return config


def save_rag_config(config: Dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    current = load_rag_config()
    current.update(config)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
