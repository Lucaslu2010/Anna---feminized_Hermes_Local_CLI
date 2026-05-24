import json
import os
from typing import Dict
from urllib.parse import urlparse


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".anna_web")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


DEFAULT_WEB_CONFIG = {
    "web_mode_enabled": False,
    "server_url": "http://127.0.0.1:8765",
    "api_key": "",
    "auth_token": "",
    "username": "",
    "role": "",
    "directory_blocking_enabled": True,
    "location_injection_enabled": True,
}
DEFAULT_WEB_PORT = 8765


def load_web_config() -> Dict:
    config = dict(DEFAULT_WEB_CONFIG)

    if not os.path.exists(CONFIG_PATH):
        return config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return config

    if isinstance(saved, dict):
        config.update(saved)

    config["server_url"] = normalize_server_url(config.get("server_url", ""))
    config["web_mode_enabled"] = bool(config.get("web_mode_enabled"))
    config["auth_token"] = str(config.get("auth_token", ""))
    config["username"] = str(config.get("username", ""))
    config["role"] = str(config.get("role", ""))
    config["directory_blocking_enabled"] = bool(config.get("directory_blocking_enabled", True))
    config["location_injection_enabled"] = bool(config.get("location_injection_enabled", True))
    return config


def save_web_config(values: Dict) -> str:
    os.makedirs(CONFIG_DIR, exist_ok=True)

    config = load_web_config()
    config.update(values or {})
    config["server_url"] = normalize_server_url(config.get("server_url", ""))
    config["web_mode_enabled"] = bool(config.get("web_mode_enabled"))
    config["auth_token"] = str(config.get("auth_token", ""))
    config["username"] = str(config.get("username", ""))
    config["role"] = str(config.get("role", ""))
    config["directory_blocking_enabled"] = bool(config.get("directory_blocking_enabled", True))
    config["location_injection_enabled"] = bool(config.get("location_injection_enabled", True))

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass

    return CONFIG_PATH


def is_web_mode_enabled() -> bool:
    return bool(load_web_config().get("web_mode_enabled"))


def normalize_server_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        value = DEFAULT_WEB_CONFIG["server_url"]

    if "://" not in value:
        value = f"http://{value}"

    parsed = urlparse(value)
    if not parsed.netloc:
        return DEFAULT_WEB_CONFIG["server_url"]

    try:
        port = parsed.port
    except ValueError:
        return DEFAULT_WEB_CONFIG["server_url"]

    if port is None:
        hostname = parsed.hostname or parsed.netloc
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = f"{hostname}:{DEFAULT_WEB_PORT}"
        if parsed.username or parsed.password:
            auth = parsed.username or ""
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"
        value = parsed._replace(netloc=netloc).geturl()

    return value.rstrip("/")
