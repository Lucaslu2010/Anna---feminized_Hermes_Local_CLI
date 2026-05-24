import json
import os
from typing import Dict


CONFIG_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "rag_config.json")
LEGACY_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".anna_rag")
LEGACY_CONFIG_PATH = os.path.join(LEGACY_CONFIG_DIR, "config.json")


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
    path = resolve_rag_config_path()
    config["_config_path"] = path
    config["_config_exists"] = bool(path and os.path.exists(path))

    if not path or not os.path.exists(path):
        return config

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            saved = json.load(f)
    except Exception as e:
        config["_config_error"] = str(e)
        return config

    if isinstance(saved, dict):
        config.update(saved)

    config = migrate_legacy_config(config)
    return config


def resolve_rag_config_path() -> str:
    configured = os.environ.get("ANNA_RAG_CONFIG_PATH", "").strip().strip('"')
    if configured:
        return os.path.abspath(os.path.expanduser(configured))

    candidates = [
        CONFIG_PATH,
        os.path.join(CONFIG_DIR, "config.json"),
        os.path.join(CONFIG_DIR, "config"),
        os.path.join(CONFIG_DIR, "config.json.txt"),
        LEGACY_CONFIG_PATH,
        os.path.join(LEGACY_CONFIG_DIR, "config"),
        os.path.join(LEGACY_CONFIG_DIR, "config.json.txt"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return CONFIG_PATH


def rag_config_diagnostics() -> Dict:
    config = load_rag_config()
    key = str(config.get("embedding_api_key") or "").strip()
    return {
        "config_dir": CONFIG_DIR,
        "config_path": config.get("_config_path") or resolve_rag_config_path(),
        "config_exists": bool(config.get("_config_exists")),
        "config_error": config.get("_config_error", ""),
        "preferred_config_path": CONFIG_PATH,
        "legacy_config_path": LEGACY_CONFIG_PATH,
        "embedding_provider": config.get("embedding_provider", ""),
        "embedding_api_style": config.get("embedding_api_style", ""),
        "embedding_base_url": config.get("embedding_base_url", ""),
        "embedding_model": config.get("embedding_model", ""),
        "embedding_dimensions": config.get("embedding_dimensions", ""),
        "embedding_api_key_present": bool(key),
        "embedding_api_key_length": len(key),
        "env_key_present": bool(
            os.environ.get("SILICONFLOW_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        ),
    }


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
    for key in list(current.keys()):
        if key.startswith("_config_"):
            current.pop(key, None)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
