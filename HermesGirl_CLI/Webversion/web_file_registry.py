import json
import os
import time
import uuid
from typing import Dict, Optional


REGISTRY_DIR = os.path.join(os.path.expanduser("~"), ".anna_web")
REGISTRY_PATH = os.path.join(REGISTRY_DIR, "file_keys.json")


def load_file_registry() -> Dict:
    if not os.path.exists(REGISTRY_PATH):
        return {"files": {}}

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"files": {}}

    if not isinstance(data, dict):
        return {"files": {}}

    files = data.get("files")
    if not isinstance(files, dict):
        data["files"] = {}
    return data


def save_file_registry(data: Dict) -> str:
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    if not isinstance(data.get("files"), dict):
        data["files"] = {}

    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    try:
        os.chmod(REGISTRY_PATH, 0o600)
    except Exception:
        pass

    return REGISTRY_PATH


def create_file_key() -> str:
    return uuid.uuid4().hex


def remember_local_file(
    path: str,
    key: Optional[str] = None,
    server_url: str = "",
    username: str = "",
) -> Dict:
    data = load_file_registry()
    path = os.path.abspath(os.path.expanduser(path or "")) if path else ""
    filename = os.path.basename(path) if path else ""
    existing_key = key or find_key_for_path(path, server_url=server_url, username=username)
    key = existing_key or create_file_key()

    record = data["files"].get(key, {})
    record.update(
        {
            "key": key,
            "filename": filename or record.get("filename", ""),
            "local_path": path,
            "server_url": server_url or record.get("server_url", ""),
            "username": username or record.get("username", ""),
            "updated_at": time.time(),
            "forgotten": False,
        }
    )
    data["files"][key] = record
    save_file_registry(data)
    return record


def get_file_record(key: str) -> Dict:
    return load_file_registry().get("files", {}).get(key, {})


def mark_uploaded(key: str, filename: str = "", server_url: str = "", username: str = ""):
    update_file_record(
        key,
        {
            "filename": filename,
            "server_url": server_url,
            "username": username,
            "last_uploaded_at": time.time(),
            "forgotten": False,
        },
    )


def mark_forgotten(key: str):
    update_file_record(key, {"forgotten": True, "updated_at": time.time()})


def update_file_record(key: str, updates: Dict):
    if not key:
        return

    data = load_file_registry()
    record = data["files"].get(key, {"key": key})
    for name, value in (updates or {}).items():
        if value is not None:
            record[name] = value
    record["updated_at"] = time.time()
    data["files"][key] = record
    save_file_registry(data)


def find_key_for_path(path: str, server_url: str = "", username: str = "") -> str:
    if not path:
        return ""

    path = os.path.abspath(os.path.expanduser(path))
    for key, record in load_file_registry().get("files", {}).items():
        if record.get("local_path") != path:
            continue
        if server_url and record.get("server_url") not in ["", server_url]:
            continue
        if username and record.get("username") not in ["", username]:
            continue
        return key
    return ""
