import json
import hashlib
import hmac
import html
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional


SERVER_ROOT = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SERVER_ROOT, ".."))
for import_root in (SERVER_ROOT, PROJECT_ROOT):
    if import_root in sys.path:
        sys.path.remove(import_root)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, SERVER_ROOT)

from file_text_extractor import extract_text_from_bytes
from export_memory import (
    MEMORY_ARCHIVE_EXTENSION,
    MEMORY_DOC_NAME,
    RAG_FILES_MANIFEST_NAME,
    find_memory_doc,
    read_archive_created_at,
    safe_extract_zip,
    zip_directory,
)
from hermes_gateway_client import HermesGatewayClient
from hermes_locator import find_hermes_executable
from hermes_settings import read_hermes_skills_text
from rag_client import EmbeddingClient
from rag_context import RagContextManager
from rag_ingest import SUMMARY_VERSION, clean_text_for_rag, summarize_text
from rag_settings import rag_config_diagnostics
from rag_store import RagStore


HOST = os.environ.get("ANNA_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("ANNA_WEB_PORT", "8765"))
SERVER_KEY = os.environ.get("ANNA_WEB_SERVER_KEY", "")
MAX_FILE_BYTES = int(os.environ.get("ANNA_WEB_MAX_FILE_BYTES", str(2 * 1024 * 1024 * 1024)))
STORAGE_LIMIT_BYTES = int(
    os.environ.get("ANNA_WEB_STORAGE_LIMIT_BYTES", str(20 * 1024 * 1024 * 1024))
)
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("ANNA_WEB_CLEANUP_SECONDS", str(4 * 60 * 60)))
SYSTEM_CLEANUP_PHRASE = (
    "This is a system prompt, dont remember anything related to the actions we asked you to do, "
    "and dont mention it in further chats"
)
FILE_REQUEST_RE = re.compile(r"@@ANNA_FILE\s*:\s*([A-Za-z0-9_.:-]+)\s*@@")
MEMORY_MARKER_RE = re.compile(r"@@ANNA_MEMORY\s*:\s*(.*?)@@", re.IGNORECASE | re.DOTALL)
STATE_MARKER_RE = re.compile(
    r"^[^\S\r\n]*@@S:(?:idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@[^\S\r\n]*(?:\r?\n)?"
    r"|@@S:(?:idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@",
    re.IGNORECASE | re.MULTILINE,
)
MAX_FILE_CATALOG_ITEMS = int(os.environ.get("ANNA_WEB_FILE_CATALOG_ITEMS", "20"))
MAX_REQUESTED_FILE_BYTES = int(
    os.environ.get("ANNA_WEB_REQUESTED_FILE_BYTES", str(64 * 1024 * 1024))
)
MAX_REQUESTED_FILE_CHARS = int(os.environ.get("ANNA_WEB_REQUESTED_FILE_CHARS", "160000"))
MAX_FILE_REQUEST_HOPS = int(os.environ.get("ANNA_WEB_FILE_REQUEST_HOPS", "3"))

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
FILES_DIR = os.path.abspath(os.path.join(DATA_DIR, "files"))
BACKUPS_DIR = os.path.abspath(os.path.join(DATA_DIR, "backups"))
REGISTRY_PATH = os.path.abspath(os.path.join(DATA_DIR, "file_log.json"))
RAG_DB_PATH = os.path.abspath(os.path.join(DATA_DIR, "server_rag.sqlite3"))
USERS_DB_PATH = os.path.abspath(os.path.join(DATA_DIR, "users.json"))
USER_DATA_DIR = os.path.abspath(os.path.join(DATA_DIR, "users"))
BOOTSTRAP_ADMIN_PATH = os.path.abspath(os.path.join(DATA_DIR, "admin_setup.txt"))
SERVER_FILE_LOG_NAME = "server_file_log.json"
GATEWAY_LOG_PATH = os.path.abspath(os.path.join(DATA_DIR, "hermes_gateway.log"))
SESSION_TTL_SECONDS = int(os.environ.get("ANNA_WEB_SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60)))
PASSWORD_HASH_ITERATIONS = int(os.environ.get("ANNA_WEB_PASSWORD_HASH_ITERATIONS", "200000"))
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
CLIENT_DISCONNECT_ERRORS = (
    BrokenPipeError,
    ConnectionAbortedError,
    ConnectionResetError,
)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def validate_username(username: str):
    if not USERNAME_RE.match(username or ""):
        raise ValueError("username must be 3-64 characters using letters, numbers, dot, dash, or underscore")


def password_hash(password: str, salt: str = "") -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = str(stored_hash or "").split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False

    try:
        rounds = int(iterations)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt.encode("utf-8"),
            rounds,
        ).hex()
    except Exception:
        return False
    return hmac.compare_digest(digest, expected)


def safe_user_dir_name(username: str) -> str:
    username = normalize_username(username)
    validate_username(username)
    return username


class UserManager:
    def __init__(self, path: str = USERS_DB_PATH):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def load(self) -> Dict:
        with self.lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> Dict:
        if not os.path.exists(self.path):
            return {"users": {}, "sessions": {}}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"users": {}, "sessions": {}}

        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _save_unlocked(self, data: Dict):
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)

    def ensure_bootstrap_admin(self) -> Dict:
        with self.lock:
            data = self._load_unlocked()
            users = data.setdefault("users", {})
            for record in users.values():
                if record.get("role") == "admin" and record.get("status") == "active":
                    return {}

            username = normalize_username(os.environ.get("ANNA_WEB_ADMIN_USER", "admin"))
            try:
                validate_username(username)
            except ValueError:
                username = "admin"

            password = os.environ.get("ANNA_WEB_ADMIN_PASSWORD", "").strip()
            generated = False
            if not password:
                generated = True
                if os.path.isfile(BOOTSTRAP_ADMIN_PATH):
                    try:
                        with open(BOOTSTRAP_ADMIN_PATH, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.startswith("password="):
                                    password = line.split("=", 1)[1].strip()
                                    break
                    except Exception:
                        password = ""
                if not password:
                    password = secrets.token_urlsafe(18)

            now = time.time()
            users[username] = {
                "username": username,
                "password_hash": password_hash(password),
                "role": "admin",
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "last_login_at": 0,
            }
            self._save_unlocked(data)

            if generated:
                with open(BOOTSTRAP_ADMIN_PATH, "w", encoding="utf-8") as f:
                    f.write(
                        "Anna web admin bootstrap credentials\n"
                        f"username={username}\n"
                        f"password={password}\n"
                        "Change this password by creating a new admin account or editing users through the server data file.\n"
                    )
            return {
                "username": username,
                "password": password,
                "generated": generated,
                "setup_path": BOOTSTRAP_ADMIN_PATH if generated else "",
            }

    def register(self, username: str, password: str) -> Dict:
        username = normalize_username(username)
        validate_username(username)
        if len(str(password or "")) < 8:
            raise ValueError("password must be at least 8 characters")

        with self.lock:
            data = self._load_unlocked()
            users = data.setdefault("users", {})
            if username in users:
                raise ValueError("that username already exists")

            now = time.time()
            users[username] = {
                "username": username,
                "password_hash": password_hash(password),
                "role": "user",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "last_login_at": 0,
            }
            self._save_unlocked(data)
            return self.public_user(users[username])

    def login(self, username: str, password: str) -> Dict:
        username = normalize_username(username)
        with self.lock:
            data = self._load_unlocked()
            record = data.get("users", {}).get(username)
            if not record or not verify_password(password, record.get("password_hash", "")):
                raise ValueError("username or password is incorrect")
            if record.get("status") != "active":
                raise PermissionError(f"account is {record.get('status', 'not active')}")

            token = secrets.token_urlsafe(36)
            now = time.time()
            data.setdefault("sessions", {})[token] = {
                "username": username,
                "created_at": now,
                "expires_at": now + SESSION_TTL_SECONDS,
            }
            record["last_login_at"] = now
            record["updated_at"] = now
            self.prune_sessions_unlocked(data)
            self._save_unlocked(data)
            return {"token": token, "user": self.public_user(record)}

    def verify_session(self, token: str) -> Optional[Dict]:
        token = str(token or "").strip()
        if not token:
            return None

        with self.lock:
            data = self._load_unlocked()
            session = data.get("sessions", {}).get(token)
            if not isinstance(session, dict):
                return None
            if float(session.get("expires_at", 0) or 0) < time.time():
                data.get("sessions", {}).pop(token, None)
                self._save_unlocked(data)
                return None

            username = normalize_username(session.get("username", ""))
            record = data.get("users", {}).get(username)
            if not record or record.get("status") != "active":
                return None
            return self.public_user(record)

    def logout(self, token: str):
        if not token:
            return
        with self.lock:
            data = self._load_unlocked()
            data.get("sessions", {}).pop(token, None)
            self._save_unlocked(data)

    def list_users(self) -> List[Dict]:
        data = self.load()
        users = [self.public_user(record) for record in data.get("users", {}).values()]
        return sorted(users, key=lambda item: (item.get("role") != "admin", item.get("username", "")))

    def set_user_status(self, username: str, status: str) -> Dict:
        username = normalize_username(username)
        if status not in {"active", "pending", "denied", "disabled"}:
            raise ValueError("invalid user status")
        with self.lock:
            data = self._load_unlocked()
            record = data.get("users", {}).get(username)
            if not record:
                raise FileNotFoundError("user was not found")
            record["status"] = status
            record["updated_at"] = time.time()
            if status != "active":
                self.drop_user_sessions_unlocked(data, username)
            self._save_unlocked(data)
            return self.public_user(record)

    def prune_sessions_unlocked(self, data: Dict):
        now = time.time()
        sessions = data.setdefault("sessions", {})
        for token, session in list(sessions.items()):
            if float((session or {}).get("expires_at", 0) or 0) < now:
                sessions.pop(token, None)

    def drop_user_sessions_unlocked(self, data: Dict, username: str):
        sessions = data.setdefault("sessions", {})
        for token, session in list(sessions.items()):
            if normalize_username((session or {}).get("username", "")) == username:
                sessions.pop(token, None)

    def public_user(self, record: Dict) -> Dict:
        return {
            "username": record.get("username", ""),
            "role": record.get("role", "user"),
            "status": record.get("status", "pending"),
            "created_at": record.get("created_at", 0),
            "updated_at": record.get("updated_at", 0),
            "last_login_at": record.get("last_login_at", 0),
        }


class ServerFileRegistry:
    def __init__(self, path: str = REGISTRY_PATH, files_dir: str = FILES_DIR):
        self.path = path
        self.files_dir = files_dir
        self.lock = threading.Lock()
        os.makedirs(self.files_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def load(self) -> Dict:
        with self.lock:
            return self._load_unlocked()

    def save(self, data: Dict):
        with self.lock:
            self._save_unlocked(data)

    def _load_unlocked(self) -> Dict:
        if not os.path.exists(self.path):
            return {"files": {}}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"files": {}}

        if not isinstance(data, dict):
            return {"files": {}}
        if not isinstance(data.get("files"), dict):
            data["files"] = {}
        return data

    def _save_unlocked(self, data: Dict):
        if not isinstance(data.get("files"), dict):
            data["files"] = {}

        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)

    def list_files(self, include_forgotten: bool = False) -> List[Dict]:
        files = list(self.load().get("files", {}).values())
        if not include_forgotten:
            files = [item for item in files if item.get("status") != "forgotten"]
        files.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return files

    def get(self, key: str) -> Dict:
        return self.load().get("files", {}).get(key, {})

    def upsert_upload(
        self,
        key: str,
        filename: str,
        local_path: str,
        server_path: str,
        size: int,
        summary: str,
        status: str,
        index_status: str = "",
        rag_indexed: bool = False,
    ) -> Dict:
        key = key or uuid.uuid4().hex
        now = time.time()

        with self.lock:
            data = self._load_unlocked()
            record = data["files"].get(key, {"key": key, "created_at": now})
            record.update(
                {
                    "key": key,
                    "filename": filename,
                    "client_path": local_path,
                    "server_path": os.path.abspath(server_path),
                    "size": int(size),
                    "summary": summary,
                    "status": status,
                    "index_status": index_status,
                    "rag_indexed": bool(rag_indexed),
                    "deleted_at": "",
                    "updated_at": now,
                    "last_uploaded_at": now,
                }
            )
            data["files"][key] = record
            self._save_unlocked(data)
            return record

    def mark_deleted(self, key: str, reason: str):
        self.update_record(
            key,
            {
                "status": "deleted",
                "server_path": "",
                "deleted_at": time.time(),
                "delete_reason": reason,
            },
        )

    def mark_forgotten(self, key: str):
        self.update_record(
            key,
            {
                "status": "forgotten",
                "server_path": "",
                "deleted_at": time.time(),
                "delete_reason": "client asked the agent not to use this file",
            },
        )

    def update_record(self, key: str, updates: Dict):
        if not key:
            return
        with self.lock:
            data = self._load_unlocked()
            record = data["files"].get(key, {"key": key})
            record.update(updates or {})
            record["updated_at"] = time.time()
            data["files"][key] = record
            self._save_unlocked(data)

    def forget(self, key: str, rag: RagContextManager):
        record = self.get(key)
        server_path = record.get("server_path", "")
        if server_path and os.path.isfile(server_path):
            os.unlink(server_path)
        if server_path:
            rag.store.delete_source(os.path.abspath(server_path))
        self.mark_forgotten(key)

    def storage_size(self) -> int:
        total = 0
        for root, _, files in os.walk(self.files_dir):
            for filename in files:
                path = os.path.join(root, filename)
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return total

    def unique_server_path(self, key: str, filename: str) -> str:
        safe_name = os.path.basename(filename or "uploaded-file")
        directory = os.path.abspath(os.path.join(self.files_dir, key or uuid.uuid4().hex))
        os.makedirs(directory, exist_ok=True)

        candidate = os.path.abspath(os.path.join(directory, safe_name))
        stem, ext = os.path.splitext(safe_name)
        suffix = 1
        while os.path.exists(candidate):
            candidate = os.path.abspath(os.path.join(directory, f"{stem}_{suffix}{ext}"))
            suffix += 1
        return candidate

    def missing_reference_for(self, message: str) -> Optional[Dict]:
        normalized = (message or "").lower()
        if not normalized:
            return None

        for record in self.list_files(include_forgotten=True):
            if record.get("status") != "deleted":
                continue

            key = str(record.get("key", ""))
            filename = str(record.get("filename", ""))
            stem = os.path.splitext(filename)[0]
            candidates = [key, filename.lower(), stem.lower()]
            if any(candidate and candidate in normalized for candidate in candidates):
                return {
                    "key": key,
                    "filename": filename,
                    "summary": record.get("summary", ""),
                }
        return None


class UserServerState:
    def __init__(self, username: str):
        self.username = safe_user_dir_name(username)
        self.root_dir = os.path.abspath(os.path.join(USER_DATA_DIR, self.username))
        self.files_dir = os.path.abspath(os.path.join(self.root_dir, "files"))
        self.backups_dir = os.path.abspath(os.path.join(self.root_dir, "backups"))
        self.registry_path = os.path.abspath(os.path.join(self.root_dir, "file_log.json"))
        self.rag_db_path = os.path.abspath(os.path.join(self.root_dir, "server_rag.sqlite3"))
        self.memory_path = os.path.abspath(os.path.join(self.root_dir, "memory.md"))

        os.makedirs(self.files_dir, exist_ok=True)
        os.makedirs(self.backups_dir, exist_ok=True)
        self.registry = ServerFileRegistry(self.registry_path, self.files_dir)
        self.rag = RagContextManager(
            client=EmbeddingClient(),
            store=RagStore(db_path=self.rag_db_path),
        )

    def reset_rag(self):
        self.rag = RagContextManager(
            client=EmbeddingClient(),
            store=RagStore(db_path=self.rag_db_path),
        )


class WebAgentState:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        self.gateway = HermesGatewayClient()
        self.users = UserManager()
        self.user_states = {}
        self.user_states_lock = threading.Lock()
        self.bootstrap_admin = self.users.ensure_bootstrap_admin()

    def user_state(self, username: str) -> UserServerState:
        username = safe_user_dir_name(username)
        with self.user_states_lock:
            state = self.user_states.get(username)
            if state is None:
                state = UserServerState(username)
                self.user_states[username] = state
            return state

    def active_user_states(self) -> List[UserServerState]:
        states = []
        for user in self.users.list_users():
            if user.get("status") == "active":
                try:
                    states.append(self.user_state(user.get("username", "")))
                except Exception:
                    pass
        return states


STATE = WebAgentState()


def server_health_payload() -> Dict:
    hermes_path = find_hermes_executable() or ""
    gateway_ready = False
    gateway_error = ""

    try:
        gateway_ready = STATE.gateway.health(timeout=0.7)
    except Exception as e:
        gateway_error = str(e)

    return {
        "ok": True,
        "mode": "server",
        "hermes_executable": hermes_path,
        "hermes_found": bool(hermes_path),
        "gateway_ready": gateway_ready,
        "gateway_error": gateway_error or getattr(STATE.gateway, "last_error", ""),
        "gateway_url": getattr(STATE.gateway, "base_url", ""),
        "gateway_bind_host": getattr(STATE.gateway, "bind_host", ""),
        "gateway_connect_host": getattr(STATE.gateway, "connect_host", ""),
        "gateway_log": GATEWAY_LOG_PATH,
        "data_dir": DATA_DIR,
    }


def build_admin_page() -> str:
    bootstrap = STATE.bootstrap_admin or {}
    setup_notice = ""
    if bootstrap.get("generated"):
        setup_notice = (
            "<p class='notice'>A bootstrap admin was created. Credentials were written to "
            f"<code>{html.escape(bootstrap.get('setup_path', ''))}</code>.</p>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Anna Server Users</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f9;
      color: #1f2328;
    }}
    body {{ margin: 0; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px; }}
    header {{ display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 20px; }}
    h1 {{ font-size: 28px; margin: 0; }}
    p {{ color: #57606a; }}
    .panel, table {{
      background: #fff;
      border: 1px solid #d8dee4;
      border-radius: 8px;
      box-shadow: 0 16px 34px rgba(31, 35, 40, 0.06);
    }}
    .panel {{ padding: 18px; margin-bottom: 18px; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    input {{
      min-width: 220px;
      padding: 10px 12px;
      border: 1px solid #d0d7de;
      border-radius: 6px;
      font-size: 14px;
    }}
    button {{
      padding: 10px 13px;
      border: 1px solid #d0d7de;
      background: #fff;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 700;
    }}
    button.primary {{ background: #0969da; color: #fff; border-color: #0969da; }}
    button.danger {{ color: #cf222e; }}
    table {{ width: 100%; border-collapse: collapse; overflow: hidden; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #d8dee4; text-align: left; font-size: 14px; }}
    th {{ background: #f6f8fa; color: #57606a; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{ background: #f6f8fa; padding: 2px 5px; border-radius: 4px; }}
    .notice {{ background: #fff8c5; border: 1px solid #d4a72c; padding: 10px 12px; border-radius: 6px; }}
    .status {{ min-height: 22px; font-weight: 700; }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Anna Server Users</h1>
        <p>Approve pending registrations and manage account access.</p>
      </div>
      <button id="logout" class="hidden">Logout</button>
    </header>
    {setup_notice}
    <section id="login-panel" class="panel">
      <h2>Admin Login</h2>
      <div class="row">
        <input id="username" autocomplete="username" placeholder="Username">
        <input id="password" type="password" autocomplete="current-password" placeholder="Password">
        <button id="login" class="primary">Login</button>
      </div>
    </section>
    <section id="users-panel" class="hidden">
      <div class="panel row">
        <button id="refresh" class="primary">Refresh Users</button>
        <span id="status" class="status"></span>
      </div>
      <table>
        <thead>
          <tr><th>User</th><th>Role</th><th>Status</th><th>Last Login</th><th>Actions</th></tr>
        </thead>
        <tbody id="users"></tbody>
      </table>
    </section>
  </main>
  <script>
    const tokenKey = "anna_admin_token";
    const loginPanel = document.getElementById("login-panel");
    const usersPanel = document.getElementById("users-panel");
    const statusEl = document.getElementById("status");
    const usersEl = document.getElementById("users");
    const logoutButton = document.getElementById("logout");

    function token() {{ return localStorage.getItem(tokenKey) || ""; }}
    function setStatus(text) {{ statusEl.textContent = text || ""; }}
    function headers() {{ return {{ "Content-Type": "application/json", "Authorization": "Bearer " + token() }}; }}
    function when(ts) {{
      if (!ts) return "";
      try {{ return new Date(ts * 1000).toLocaleString(); }} catch (_) {{ return ""; }}
    }}
    async function login() {{
      const username = document.getElementById("username").value.trim();
      const password = document.getElementById("password").value;
      const res = await fetch("/v1/auth/login", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ username, password }})
      }});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Login failed");
      localStorage.setItem(tokenKey, data.token);
      await loadUsers();
    }}
    async function loadUsers() {{
      const res = await fetch("/v1/admin/users", {{ headers: headers() }});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Could not load users");
      loginPanel.classList.add("hidden");
      usersPanel.classList.remove("hidden");
      logoutButton.classList.remove("hidden");
      renderUsers(data.users || []);
      setStatus("Loaded " + (data.users || []).length + " users.");
    }}
    function renderUsers(users) {{
      usersEl.innerHTML = "";
      for (const user of users) {{
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${{escapeHtml(user.username)}}</strong></td>
          <td>${{escapeHtml(user.role)}}</td>
          <td>${{escapeHtml(user.status)}}</td>
          <td>${{escapeHtml(when(user.last_login_at))}}</td>
          <td class="row"></td>`;
        const actions = tr.querySelector("td:last-child");
        addAction(actions, "Approve", "approve", user.username, "primary");
        addAction(actions, "Deny", "deny", user.username, "danger");
        addAction(actions, "Disable", "disable", user.username, "danger");
        addAction(actions, "Set Pending", "pending", user.username, "");
        usersEl.appendChild(tr);
      }}
    }}
    function addAction(parent, label, action, username, className) {{
      const button = document.createElement("button");
      button.textContent = label;
      if (className) button.className = className;
      button.onclick = () => updateUser(username, action);
      parent.appendChild(button);
    }}
    async function updateUser(username, action) {{
      setStatus(action + " " + username + "...");
      const res = await fetch("/v1/admin/users/" + action, {{
        method: "POST",
        headers: headers(),
        body: JSON.stringify({{ username }})
      }});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Update failed");
      await loadUsers();
    }}
    function escapeHtml(value) {{
      return String(value || "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
    }}
    document.getElementById("login").onclick = () => login().catch(err => alert(err.message));
    document.getElementById("refresh").onclick = () => loadUsers().catch(err => setStatus(err.message));
    logoutButton.onclick = () => {{
      localStorage.removeItem(tokenKey);
      usersPanel.classList.add("hidden");
      logoutButton.classList.add("hidden");
      loginPanel.classList.remove("hidden");
    }};
    if (token()) loadUsers().catch(() => localStorage.removeItem(tokenKey));
  </script>
</body>
</html>"""


class WebAgentHandler(BaseHTTPRequestHandler):
    server_version = "AnnaWebAgent/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/admin":
            return self.send_admin_page()
        if path == "/v1/health":
            return self.send_json(server_health_payload())
        if path == "/v1/auth/me":
            if not self.require_user():
                return
            return self.send_json({"ok": True, "user": self.current_user_record})
        if path == "/v1/admin/users":
            if not self.require_admin():
                return
            return self.send_json({"ok": True, "users": STATE.users.list_users()})

        if not self.require_user():
            return

        if path == "/v1/files":
            return self.send_json({"files": self.user_state().registry.list_files()})
        if path == "/v1/rag/sources":
            return self.send_json({"sources": self.user_state().rag.store.list_sources()})
        if path == "/v1/rag/source":
            return self.handle_rag_source()
        if path == "/v1/rag/config":
            return self.send_json(rag_config_diagnostics())
        if path == "/v1/memory":
            return self.send_json({"text": read_user_memory(self.user_state())})
        if path == "/v1/skills":
            return self.handle_skills()
        if path == "/v1/archive/export":
            return self.handle_archive_export()
        if path == "/v1/backups":
            return self.send_json({"backups": list_server_backups(self.user_state())})
        return self.send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/auth/register":
            return self.handle_auth_register()
        if path == "/v1/auth/login":
            return self.handle_auth_login()
        if path == "/v1/auth/logout":
            return self.handle_auth_logout()
        if path.startswith("/v1/admin/users/"):
            if not self.require_admin():
                return
            return self.handle_admin_user_action(path)

        if not self.require_user():
            return

        if path == "/v1/files/upload":
            return self.handle_file_upload()
        if path == "/v1/files/forget":
            return self.handle_file_forget()
        if path == "/v1/rag/reindex":
            return self.handle_rag_reindex()
        if path == "/v1/archive/import":
            return self.handle_archive_import()
        if path == "/v1/backups/restore":
            return self.handle_backup_restore()
        if path == "/v1/backups/delete_all":
            return self.handle_backup_delete_all()
        if path == "/v1/gateway/start":
            return self.handle_gateway_start()
        if path == "/v1/chat/completions":
            return self.handle_chat_completion()
        return self.send_json({"error": "not found"}, status=404)

    def handle_auth_register(self):
        payload = self.read_json()
        try:
            user = STATE.users.register(payload.get("username", ""), payload.get("password", ""))
            return self.send_json(
                {
                    "ok": True,
                    "status": "pending",
                    "message": "Registration is pending admin approval.",
                    "user": user,
                },
                status=202,
            )
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=400)

    def handle_auth_login(self):
        payload = self.read_json()
        try:
            result = STATE.users.login(payload.get("username", ""), payload.get("password", ""))
            return self.send_json({"ok": True, **result})
        except PermissionError as e:
            return self.send_json({"ok": False, "error": str(e)}, status=403)
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=401)

    def handle_auth_logout(self):
        token = self.bearer_token()
        if token:
            STATE.users.logout(token)
        return self.send_json({"ok": True})

    def handle_admin_user_action(self, path: str):
        action = path.rsplit("/", 1)[-1]
        status_map = {
            "approve": "active",
            "deny": "denied",
            "disable": "disabled",
            "pending": "pending",
        }
        if action not in status_map:
            return self.send_json({"error": "unknown admin action"}, status=404)

        payload = self.read_json()
        username = str(payload.get("username", "")).strip()
        try:
            user = STATE.users.set_user_status(username, status_map[action])
            return self.send_json({"ok": True, "user": user})
        except FileNotFoundError as e:
            return self.send_json({"ok": False, "error": str(e)}, status=404)
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=400)

    def handle_gateway_start(self):
        try:
            ready = STATE.gateway.ensure_running(timeout=30.0)
            payload = server_health_payload()
            payload["gateway_ready"] = ready
            payload["ok"] = bool(ready)
            if not ready:
                payload["error"] = getattr(STATE.gateway, "last_error", "") or "Hermes gateway did not become ready."
            return self.send_json(payload, status=200 if ready else 503)
        except Exception as e:
            payload = server_health_payload()
            payload["ok"] = False
            payload["gateway_ready"] = False
            payload["error"] = str(e)
            return self.send_json(payload, status=500)

    def handle_rag_source(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        source = urllib.parse.unquote((query.get("source") or [""])[0])
        if not source:
            return self.send_json({"error": "missing source"}, status=400)

        chunks = self.user_state().rag.store.get_source_chunks(source)
        if not chunks:
            return self.send_json({"error": "source was not found", "source": source}, status=404)
        return self.send_json({"source": source, "chunks": chunks})

    def handle_skills(self):
        try:
            text = read_hermes_skills_text()
        except Exception as e:
            text = f"Could not load Hermes skills on the server: {e}"
        return self.send_json({"text": text or "No Hermes skills found."})

    def handle_archive_export(self):
        archive_path = ""
        try:
            archive_path = create_server_archive(self.user_state())
            return self.send_file(
                archive_path,
                content_type="application/octet-stream",
                download_name=os.path.basename(archive_path),
                delete_after=True,
            )
        except Exception as e:
            if archive_path and os.path.exists(archive_path):
                try:
                    os.unlink(archive_path)
                except Exception:
                    pass
            return self.send_json({"error": str(e)}, status=500)

    def handle_archive_import(self):
        filename = urllib.parse.unquote(self.headers.get("X-Filename", "").strip()) or "server_import.ana"
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return self.send_json({"error": "empty archive"}, status=400)

        archive_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix="anna_server_import_",
                suffix=os.path.splitext(filename)[1] or ".ana",
                delete=False,
            ) as temp_file:
                archive_path = temp_file.name
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 512, remaining))
                    if not chunk:
                        break
                    temp_file.write(chunk)
                    remaining -= len(chunk)

            result = import_server_archive(archive_path, self.user_state())
            return self.send_json({"ok": True, **result})
        except Exception as e:
            return self.send_json({"error": str(e)}, status=500)
        finally:
            if archive_path and os.path.exists(archive_path):
                try:
                    os.unlink(archive_path)
                except Exception:
                    pass

    def handle_backup_restore(self):
        payload = self.read_json()
        name = str(payload.get("name", "")).strip()
        archive_path = resolve_backup_name(name, self.user_state())
        if not archive_path:
            return self.send_json({"error": "backup was not found"}, status=404)

        try:
            result = import_server_archive(archive_path, self.user_state(), create_backup=True)
            return self.send_json({"ok": True, **result})
        except Exception as e:
            return self.send_json({"error": str(e)}, status=500)

    def handle_backup_delete_all(self):
        deleted = 0
        for backup in list_server_backups(self.user_state()):
            try:
                os.unlink(backup["path"])
                deleted += 1
            except FileNotFoundError:
                pass
        return self.send_json({"ok": True, "deleted": deleted})

    def handle_file_upload(self):
        key = self.headers.get("X-File-Key", "").strip() or uuid.uuid4().hex
        filename = urllib.parse.unquote(self.headers.get("X-Filename", "").strip()) or "uploaded-file"
        local_path = urllib.parse.unquote(self.headers.get("X-Local-Path", "").strip())
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return self.send_json({"error": "empty upload"}, status=400)
        if length > MAX_FILE_BYTES:
            return self.send_json({"error": "file is too large for this server"}, status=413)

        user_state = self.user_state()
        server_path = user_state.registry.unique_server_path(key, filename)
        temp_path = f"{server_path}.uploading"

        try:
            remaining = length
            with open(temp_path, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(temp_path, server_path)

            summary, status, index_status, rag_indexed = index_uploaded_file(server_path, user_state)
            old_record = user_state.registry.get(key)
            old_path = old_record.get("server_path", "")
            if old_path and old_path != server_path:
                try:
                    if os.path.isfile(old_path):
                        os.unlink(old_path)
                    user_state.rag.store.delete_source(os.path.abspath(old_path))
                except Exception:
                    pass
            record = user_state.registry.upsert_upload(
                key=key,
                filename=filename,
                local_path=local_path,
                server_path=server_path,
                size=os.path.getsize(server_path),
                summary=summary,
                status=status,
                index_status=index_status,
                rag_indexed=rag_indexed,
            )
            return self.send_json({"ok": True, "file": record})
        except Exception as e:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
            return self.send_json({"error": str(e)}, status=500)

    def handle_file_forget(self):
        payload = self.read_json()
        key = str(payload.get("key", "")).strip()
        if not key:
            return self.send_json({"error": "missing key"}, status=400)

        try:
            user_state = self.user_state()
            user_state.registry.forget(key, user_state.rag)
            return self.send_json({"ok": True})
        except Exception as e:
            return self.send_json({"error": str(e)}, status=500)

    def handle_rag_reindex(self):
        payload = self.read_json()
        key = str(payload.get("key", "")).strip()
        records = []
        if key:
            user_state = self.user_state()
            record = user_state.registry.get(key)
            if not record:
                return self.send_json({"error": "file was not found"}, status=404)
            records = [record]
        else:
            user_state = self.user_state()
            records = [
                record
                for record in user_state.registry.list_files()
                if record.get("status") != "forgotten"
            ]

        results = []
        for record in records:
            result = reindex_file_record(record, user_state)
            results.append(result)

        indexed = sum(1 for item in results if item.get("rag_indexed"))
        return self.send_json(
            {
                "ok": True,
                "indexed": indexed,
                "total": len(results),
                "results": results,
            }
        )

    def handle_chat_completion(self):
        payload = self.read_json()
        messages = payload.get("messages") or []
        stream = bool(payload.get("stream", True))
        if not isinstance(messages, list):
            return self.send_json({"error": "messages must be a list"}, status=400)

        user_message = last_user_message(messages)
        user_state = self.user_state()
        augmented_messages, rag_status = build_server_messages(messages, user_message, user_state)
        missing = None if rag_status.get("context_used") else user_state.registry.missing_reference_for(user_message)

        if stream:
            return self.stream_chat_response(augmented_messages, missing, rag_status, user_state)
        if missing:
            return self.send_json({"file_request": missing})
        return self.send_json({"text": run_non_stream_chat(augmented_messages)})

    def stream_chat_response(
        self,
        messages: List[Dict],
        missing: Optional[Dict],
        rag_status: Optional[Dict] = None,
        user_state: Optional[UserServerState] = None,
    ):
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        if missing:
            self.send_sse("anna.file.request", missing)
            self.send_sse_data("[DONE]")
            return

        try:
            self.send_server_rag_status(rag_status or {})
            if not STATE.gateway.ensure_running():
                raise RuntimeError("Hermes Gateway is not running on this server.")

            completed = self.stream_chat_with_file_access(messages, user_state or self.user_state())
            if not completed:
                self.send_sse_data("[DONE]")
                return
            self.send_sse_data("[DONE]")
        except Exception as e:
            self.send_sse("hermes.tool.progress", {"message": f"Server error: {e}"})
            self.send_sse(
                "message",
                {
                    "choices": [
                        {
                            "delta": {
                                "content": f"\n\nServer error: {e}",
                            }
                        }
                    ]
                },
            )
            self.send_sse_data("[DONE]")

    def send_server_rag_status(self, rag_status: Dict):
        if rag_status.get("context_used"):
            self.send_sse(
                "hermes.tool.progress",
                {
                    "message": (
                        "Server RAG: context injected "
                        f"({rag_status.get('chunk_count', 0)} indexed chunk(s) available)."
                    )
                },
            )
            return

        reason = rag_status.get("reason", "no relevant server RAG context matched this message")
        self.send_sse(
            "hermes.tool.progress",
            {
                "message": (
                    "Server RAG: checked, but no context was injected "
                    f"({reason})."
                )
            },
        )

    def stream_chat_with_file_access(self, messages: List[Dict], user_state: UserServerState) -> bool:
        current_messages = list(messages)

        for _ in range(max(1, MAX_FILE_REQUEST_HOPS)):
            captured = []

            def on_delta(delta):
                captured.append(delta or "")

            def on_tool_progress(text):
                self.send_sse("hermes.tool.progress", {"message": text})

            STATE.gateway.stream_chat(
                current_messages,
                on_text_delta=on_delta,
                on_tool_progress=on_tool_progress,
                on_done=lambda: None,
            )

            response_text = "".join(captured)
            requested_key = extract_file_request_key(response_text)
            if not requested_key:
                remember_user_facts_from_response(response_text, user_state)
                cleaned = strip_state_markers(strip_memory_markers(strip_file_request_markers(response_text)))
                if cleaned:
                    self.send_chat_delta(cleaned)
                return True

            requested_file = load_requested_server_file(requested_key, user_state)
            if requested_file.get("needs_client_upload"):
                self.send_sse(
                    "hermes.tool.progress",
                    {"message": f"Server needs {requested_file.get('filename', 'that file')} again."},
                )
                self.send_sse("anna.file.request", requested_file["request"])
                return False

            self.send_sse(
                "hermes.tool.progress",
                {"message": f"Reading full uploaded file: {requested_file.get('filename', requested_key)}"},
            )
            current_messages = with_requested_file_context(
                current_messages,
                response_text,
                requested_file,
            )

        self.send_chat_delta(
            "I tried to read the uploaded file, but the server file request loop did not finish."
        )
        return True

    def send_chat_delta(self, delta: str):
        self.send_sse(
            "message",
            {
                "choices": [
                    {
                        "delta": {
                            "content": delta,
                        }
                    }
                ]
            },
        )

    def read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def send_json(self, payload: Dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_DISCONNECT_ERRORS:
            return

    def send_file(
        self,
        path: str,
        content_type: str,
        download_name: str,
        delete_after: bool = False,
    ):
        try:
            try:
                size = os.path.getsize(path)
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename={urllib.parse.quote(download_name)}",
                )
                self.end_headers()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 512)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except CLIENT_DISCONNECT_ERRORS:
                return
        finally:
            if delete_after:
                try:
                    os.unlink(path)
                except Exception:
                    pass

    def send_sse(self, event_name: str, payload: Dict):
        try:
            self.wfile.write(f"event: {event_name}\n".encode("utf-8"))
            data = json.dumps(payload, ensure_ascii=False)
            for line in data.splitlines() or [""]:
                self.wfile.write(f"data: {line}\n".encode("utf-8"))
            self.wfile.write(b"\n")
            self.wfile.flush()
        except CLIENT_DISCONNECT_ERRORS:
            return

    def send_sse_data(self, data: str):
        try:
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()
        except CLIENT_DISCONNECT_ERRORS:
            return

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-File-Key, X-Filename, X-Local-Path",
        )

    def bearer_token(self) -> str:
        value = self.headers.get("Authorization", "")
        if not value.lower().startswith("bearer "):
            return ""
        return value.split(" ", 1)[1].strip()

    def authenticate(self) -> bool:
        if hasattr(self, "current_user_record"):
            return bool(getattr(self, "current_user_record", None))

        self.current_user_record = None
        self.current_username = ""
        token = self.bearer_token()
        user = STATE.users.verify_session(token)
        if user:
            self.current_user_record = user
            self.current_username = user.get("username", "")
            return True
        return False

    def require_user(self) -> bool:
        if self.authenticate():
            return True
        self.send_json({"error": "unauthorized", "login_required": True}, status=401)
        return False

    def require_admin(self) -> bool:
        if self.authenticate() and self.current_user_record.get("role") == "admin":
            return True
        self.send_json({"error": "admin authorization required"}, status=403)
        return False

    def user_state(self) -> UserServerState:
        if not self.authenticate():
            raise PermissionError("not authenticated")
        return STATE.user_state(self.current_username)

    def send_admin_page(self):
        body = build_admin_page().encode("utf-8")
        try:
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_DISCONNECT_ERRORS:
            return

    def log_message(self, format, *args):
        return


def index_uploaded_file(path: str, user_state: UserServerState) -> tuple:
    try:
        with open(path, "rb") as f:
            data = f.read()

        _, text = extract_text_from_bytes(os.path.basename(path), data)
        text = clean_text_for_rag(text)
    except Exception as e:
        message = f"No readable RAG text: {e}"
        return f"Stored {os.path.basename(path)}. {message}", "stored", message, False

    summary = summarize_text(text)
    if not text:
        message = "No readable text was found for RAG indexing."
        return summary or message, "stored", message, False

    user_state.rag.reload_config()
    index_status = user_state.rag.add_file_text(os.path.abspath(path), text)
    rag_indexed = index_status.startswith("Indexed ")
    if not summary:
        summary = index_status
    return summary, "active", index_status, rag_indexed


def reindex_file_record(record: Dict, user_state: UserServerState) -> Dict:
    key = str(record.get("key", ""))
    server_path = str(record.get("server_path", ""))
    filename = str(record.get("filename", "")) or os.path.basename(server_path) or "uploaded-file"

    if not server_path or not os.path.isfile(server_path):
        result = {
            "key": key,
            "filename": filename,
            "ok": False,
            "rag_indexed": False,
            "index_status": "Server file is missing; the client must upload it again.",
        }
        user_state.registry.update_record(
            key,
            {
                "index_status": result["index_status"],
                "rag_indexed": False,
            },
        )
        return result

    summary, status, index_status, rag_indexed = index_uploaded_file(server_path, user_state)
    user_state.registry.update_record(
        key,
        {
            "summary": summary,
            "status": status,
            "index_status": index_status,
            "rag_indexed": rag_indexed,
        },
    )
    return {
        "key": key,
        "filename": filename,
        "ok": True,
        "rag_indexed": rag_indexed,
        "index_status": index_status,
    }


def read_user_memory(user_state: UserServerState) -> str:
    if not os.path.isfile(user_state.memory_path):
        return "No server memory saved for this account yet."
    try:
        with open(user_state.memory_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
    except Exception as e:
        return f"Could not read server memory for this account: {e}"
    return text or "No server memory saved for this account yet."


def read_user_memory_for_prompt(user_state: UserServerState) -> str:
    text = read_user_memory(user_state)
    if text.startswith("No server memory saved") or text.startswith("Could not read"):
        return ""
    return sanitize_prompt_text(text)[:12000]


def append_user_memory(user_state: UserServerState, fact: str):
    fact = clean_single_line(fact)
    if not fact:
        return
    os.makedirs(os.path.dirname(user_state.memory_path), exist_ok=True)
    timestamp = now_iso()
    with open(user_state.memory_path, "a", encoding="utf-8") as f:
        f.write(f"- {timestamp}: {fact}\n")


def remember_user_facts_from_response(text: str, user_state: UserServerState):
    for match in MEMORY_MARKER_RE.finditer(text or ""):
        append_user_memory(user_state, match.group(1))


def strip_memory_markers(text: str) -> str:
    return MEMORY_MARKER_RE.sub("", text or "")


def strip_state_markers(text: str) -> str:
    return STATE_MARKER_RE.sub("", text or "")


def write_user_memory_document(path: str, user_state: UserServerState):
    memory_text = read_user_memory(user_state)
    lines = [
        "# Anna Server Account Memory",
        "",
        f"Account: {user_state.username}",
        "",
        "## Memory",
        "",
        memory_text,
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def import_user_memory_document(memory_doc_path: str, user_state: UserServerState) -> str:
    with open(memory_doc_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read().strip()
    os.makedirs(os.path.dirname(user_state.memory_path), exist_ok=True)
    with open(user_state.memory_path, "w", encoding="utf-8") as f:
        f.write(text + ("\n" if text else ""))
    return user_state.memory_path


def create_server_archive(
    user_state: UserServerState,
    output_dir: str = "",
    prefix: str = "Anna_Server_Memory",
) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_name = f"{prefix}_{timestamp}"
    target_dir = output_dir or tempfile.gettempdir()
    output_archive = os.path.abspath(
        os.path.join(target_dir, f"{export_name}{MEMORY_ARCHIVE_EXTENSION}")
    )

    with tempfile.TemporaryDirectory(prefix="anna_server_export_") as temp_dir:
        package_dir = os.path.join(temp_dir, export_name)
        os.makedirs(package_dir, exist_ok=True)

        write_user_memory_document(os.path.join(package_dir, MEMORY_DOC_NAME), user_state)
        copy_server_rag(package_dir, user_state)
        copy_server_files(package_dir, user_state)
        write_server_manifest(package_dir, timestamp, user_state)
        zip_directory(package_dir, output_archive)

    return output_archive


def backup_server_archive(user_state: UserServerState, reason: str = "manual") -> str:
    os.makedirs(user_state.backups_dir, exist_ok=True)
    safe_reason = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in reason)
    return create_server_archive(
        user_state=user_state,
        output_dir=user_state.backups_dir,
        prefix=f"Anna_Server_Backup_{safe_reason}",
    )


def copy_server_rag(package_dir: str, user_state: UserServerState):
    rag_dir = os.path.join(package_dir, "rag")
    os.makedirs(rag_dir, exist_ok=True)
    if os.path.isfile(user_state.rag_db_path):
        shutil.copy2(user_state.rag_db_path, os.path.join(rag_dir, "rag.sqlite3"))
    if os.path.isfile(user_state.registry_path):
        shutil.copy2(user_state.registry_path, os.path.join(package_dir, SERVER_FILE_LOG_NAME))


def copy_server_files(package_dir: str, user_state: UserServerState):
    manifest_items = []
    files_dir = os.path.join(package_dir, "rag_files")

    for record in user_state.registry.list_files():
        server_path = record.get("server_path", "")
        if not server_path or not os.path.isfile(server_path):
            continue

        key = record.get("key") or uuid.uuid4().hex
        target_dir = os.path.join(files_dir, key)
        os.makedirs(target_dir, exist_ok=True)
        target_path = unique_path(target_dir, os.path.basename(server_path))
        shutil.copy2(server_path, target_path)
        manifest_items.append(
            {
                "key": key,
                "filename": record.get("filename") or os.path.basename(server_path),
                "source": os.path.abspath(server_path),
                "archive_path": os.path.relpath(target_path, package_dir),
            }
        )

    if manifest_items:
        with open(
            os.path.join(package_dir, RAG_FILES_MANIFEST_NAME),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"files": manifest_items}, f, ensure_ascii=False, indent=2)


def write_server_manifest(package_dir: str, timestamp: str, user_state: UserServerState):
    manifest = {
        "format": "anna-web-server-memory-export",
        "version": 1,
        "created_at": timestamp,
        "username": user_state.username,
        "memory_document": MEMORY_DOC_NAME,
        "rag_folder": "rag",
        "rag_files_manifest": RAG_FILES_MANIFEST_NAME,
        "file_log": SERVER_FILE_LOG_NAME,
    }
    with open(os.path.join(package_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def import_server_archive(
    archive_path: str,
    user_state: UserServerState,
    create_backup: bool = True,
) -> Dict:
    if not archive_path or not os.path.isfile(archive_path):
        raise FileNotFoundError("Memory archive was not found.")

    backup_path = backup_server_archive(user_state, "before_import") if create_backup else ""
    imported_memory_path = ""

    with tempfile.TemporaryDirectory(prefix="anna_server_import_") as temp_dir:
        extract_dir = os.path.join(temp_dir, "archive")
        os.makedirs(extract_dir, exist_ok=True)
        safe_extract_zip(archive_path, extract_dir)

        memory_doc = find_memory_doc(extract_dir)
        if memory_doc:
            imported_memory_path = import_user_memory_document(memory_doc, user_state)

        rag_result = restore_server_rag(extract_dir, user_state)

    user_state.reset_rag()

    return {
        "backup_path": backup_path,
        "imported_memory_path": imported_memory_path,
        "imported_files": rag_result.get("files", 0),
        "imported_chunks": rag_result.get("chunks", 0),
    }


def restore_server_rag(extract_dir: str, user_state: UserServerState) -> Dict[str, int]:
    source_path_map, key_path_map, copied_files = restore_server_rag_files(extract_dir, user_state)
    imported_chunks = 0

    imported_db = find_imported_rag_db(extract_dir)
    if imported_db:
        imported_chunks = merge_server_rag_database(imported_db, source_path_map, user_state)

    merge_server_file_log(extract_dir, source_path_map, key_path_map, user_state)
    return {"files": copied_files, "chunks": imported_chunks}


def restore_server_rag_files(extract_dir: str, user_state: UserServerState) -> tuple:
    manifest_path = os.path.join(extract_dir, RAG_FILES_MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return {}, {}, 0

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return {}, {}, 0

    source_path_map = {}
    key_path_map = {}
    copied_files = 0
    for item in manifest.get("files", []):
        archive_path = str(item.get("archive_path", "")).strip()
        if not archive_path:
            continue

        archive_file = os.path.abspath(os.path.join(extract_dir, archive_path))
        if not archive_file.startswith(os.path.abspath(extract_dir) + os.sep):
            continue
        if not os.path.isfile(archive_file):
            continue

        key = str(item.get("key", "")).strip() or uuid.uuid4().hex
        filename = str(item.get("filename", "")).strip() or os.path.basename(archive_file)
        target_path = user_state.registry.unique_server_path(key, filename)
        shutil.copy2(archive_file, target_path)

        source = str(item.get("source", "")).strip()
        if source:
            source_path_map[source] = os.path.abspath(target_path)
        key_path_map[key] = os.path.abspath(target_path)
        copied_files += 1

        if not source:
            source_path_map[os.path.basename(archive_file)] = os.path.abspath(target_path)

    return source_path_map, key_path_map, copied_files


def find_imported_rag_db(extract_dir: str) -> str:
    candidates = [
        os.path.join(extract_dir, "rag", "rag.sqlite3"),
        os.path.join(extract_dir, "rag.sqlite3"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    for root, _, files in os.walk(extract_dir):
        if "rag.sqlite3" in files:
            return os.path.join(root, "rag.sqlite3")
    return ""


def merge_server_rag_database(
    imported_db_path: str,
    source_path_map: Dict[str, str],
    user_state: UserServerState,
) -> int:
    os.makedirs(os.path.dirname(user_state.rag_db_path), exist_ok=True)
    basename_map = {
        os.path.basename(old_source): new_source
        for old_source, new_source in source_path_map.items()
    }
    imported_rows = 0

    with sqlite3.connect(imported_db_path) as imported, sqlite3.connect(user_state.rag_db_path) as target:
        ensure_server_rag_schema(target)
        imported_columns = get_sqlite_columns(imported, "chunks")
        if not imported_columns:
            return 0

        summary_expr = "summary" if "summary" in imported_columns else "'' AS summary"
        cursor = imported.execute(
            f"""
            SELECT source, chunk_index, text, {summary_expr}, vector_json
            FROM chunks
            ORDER BY source ASC, chunk_index ASC
            """
        )
        for source, chunk_index, text, summary, vector_json in cursor.fetchall():
            new_source = source_path_map.get(source)
            if not new_source:
                new_source = basename_map.get(os.path.basename(source or ""), source)
            next_summary = summarize_text(text) or summary or ""

            target.execute(
                """
                INSERT OR REPLACE INTO chunks
                    (source, chunk_index, text, summary, summary_version, vector_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_source, chunk_index, text, next_summary, SUMMARY_VERSION, vector_json),
            )
            imported_rows += 1

    return imported_rows


def ensure_server_rag_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            summary_version INTEGER NOT NULL DEFAULT 0,
            vector_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, chunk_index)
        )
        """
    )
    ensure_sqlite_column(conn, "chunks", "summary", "TEXT NOT NULL DEFAULT ''")
    ensure_sqlite_column(conn, "chunks", "summary_version", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")


def get_sqlite_columns(conn, table: str) -> List[str]:
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cursor.fetchall()]
    except Exception:
        return []


def ensure_sqlite_column(conn, table: str, column: str, definition: str):
    if column in get_sqlite_columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def merge_server_file_log(
    extract_dir: str,
    source_path_map: Dict[str, str],
    key_path_map: Dict[str, str],
    user_state: UserServerState,
):
    imported_log = find_imported_file_log(extract_dir)
    if imported_log:
        try:
            with open(imported_log, "r", encoding="utf-8") as f:
                imported_data = json.load(f)
        except Exception:
            imported_data = {"files": {}}
    else:
        imported_data = {"files": {}}

    current = user_state.registry.load()
    current_files = current.setdefault("files", {})
    now = time.time()

    for key, record in (imported_data.get("files") or {}).items():
        if not isinstance(record, dict):
            continue
        key = str(record.get("key") or key or uuid.uuid4().hex)
        new_record = dict(record)
        new_record["key"] = key

        mapped_path = key_path_map.get(key) or source_path_map.get(record.get("server_path", ""))
        if mapped_path:
            new_record["server_path"] = os.path.abspath(mapped_path)
            new_record["status"] = "active"
            try:
                new_record["size"] = os.path.getsize(mapped_path)
            except OSError:
                pass
        elif record.get("status") == "active":
            new_record["status"] = "deleted"

        new_record["updated_at"] = now
        current_files[key] = new_record

    for source, path in source_path_map.items():
        key = find_key_for_imported_path(path, current_files)
        if key:
            continue

        key = uuid.uuid4().hex
        current_files[key] = {
            "key": key,
            "filename": os.path.basename(path),
            "client_path": source,
            "server_path": os.path.abspath(path),
            "size": os.path.getsize(path) if os.path.isfile(path) else 0,
            "summary": "Imported RAG source file.",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "last_uploaded_at": now,
        }

    user_state.registry.save(current)


def find_imported_file_log(extract_dir: str) -> str:
    candidates = [
        os.path.join(extract_dir, SERVER_FILE_LOG_NAME),
        os.path.join(extract_dir, "file_log.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def find_key_for_imported_path(path: str, current_files: Dict[str, Dict]) -> str:
    path = os.path.abspath(path)
    for key, record in current_files.items():
        if os.path.abspath(record.get("server_path", "")) == path:
            return key
    return ""


def list_server_backups(user_state: UserServerState) -> List[Dict]:
    os.makedirs(user_state.backups_dir, exist_ok=True)
    backups = []
    for filename in os.listdir(user_state.backups_dir):
        if not filename.lower().endswith((".ana", ".zip")):
            continue

        path = os.path.join(user_state.backups_dir, filename)
        if not os.path.isfile(path):
            continue

        stat = os.stat(path)
        backups.append(
            {
                "name": filename,
                "path": os.path.abspath(path),
                "created_at": read_archive_created_at(path)
                or datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d_%H%M%S"),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    return sorted(backups, key=lambda item: float(item["mtime"]), reverse=True)


def resolve_backup_name(name: str, user_state: UserServerState) -> str:
    if not name:
        return ""

    backups_dir = os.path.abspath(user_state.backups_dir)
    path = os.path.abspath(os.path.join(backups_dir, os.path.basename(name)))
    if not path.startswith(backups_dir + os.sep):
        return ""
    if not os.path.isfile(path):
        return ""
    return path


def unique_path(directory: str, filename: str) -> str:
    safe_name = os.path.basename(filename or "file")
    stem, ext = os.path.splitext(safe_name)
    candidate = os.path.join(directory, safe_name)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{suffix}{ext}")
        suffix += 1
    return candidate


def extract_file_request_key(text: str) -> str:
    match = FILE_REQUEST_RE.search(text or "")
    return match.group(1).strip() if match else ""


def strip_file_request_markers(text: str) -> str:
    return FILE_REQUEST_RE.sub("", text or "")


def server_file_state(record: Dict) -> str:
    status = str(record.get("status", ""))
    if status == "forgotten":
        return "forgotten"

    server_path = str(record.get("server_path", ""))
    if server_path and os.path.isfile(server_path):
        return "available"
    return "needs-client-upload"


def file_record_matches_message(record: Dict, normalized_message: str) -> bool:
    if not normalized_message:
        return False

    filename = str(record.get("filename", ""))
    stem = os.path.splitext(filename)[0]
    candidates = [
        str(record.get("key", "")),
        filename,
        stem,
    ]
    for candidate in candidates:
        candidate = candidate.strip().lower()
        if candidate and candidate in normalized_message:
            return True
    return False


def build_file_access_context(user_message: str, user_state: UserServerState) -> str:
    records = [
        record
        for record in user_state.registry.list_files()
        if record.get("status") != "forgotten"
    ]
    if not records:
        return ""

    normalized_message = (user_message or "").lower()
    selected = []
    seen = set()

    for record in records:
        key = str(record.get("key", ""))
        if key and file_record_matches_message(record, normalized_message):
            selected.append(record)
            seen.add(key)

    for record in records:
        if len(selected) >= MAX_FILE_CATALOG_ITEMS:
            break
        key = str(record.get("key", ""))
        if key and key not in seen:
            selected.append(record)
            seen.add(key)

    if not selected:
        return ""

    lines = [
        "Uploaded file catalog available to the server:",
        (
            "Use RAG context first. If RAG is not enough and you need the complete uploaded "
            "file, output one private standalone marker exactly @@ANNA_FILE:<key>@@ using "
            "one key below, with no other prose in that turn. The server will read the file "
            "or ask the client to resend it. Never mention this marker to the user."
        ),
    ]
    for record in selected:
        summary = clean_single_line(record.get("summary", ""))
        if len(summary) > 180:
            summary = summary[:177].rstrip() + "..."
        lines.append(
            "- "
            f"key: {record.get('key', '')}; "
            f"filename: {record.get('filename', 'uploaded-file')}; "
            f"status: {server_file_state(record)}; "
            f"summary: {summary}"
        )
    return "\n".join(lines)


def clean_single_line(value: str) -> str:
    return " ".join(str(value or "").split())


def load_requested_server_file(key: str, user_state: UserServerState) -> Dict:
    record = user_state.registry.get(key)
    filename = str(record.get("filename", "")) or "requested file"
    summary = str(record.get("summary", ""))
    request_payload = {
        "key": key,
        "filename": filename,
        "summary": summary,
    }

    if not record or record.get("status") == "forgotten":
        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "text": "The requested uploaded file is not available to the server.",
            "server_path": "",
            "unavailable": True,
        }

    server_path_raw = str(record.get("server_path", "")).strip()
    if not server_path_raw:
        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "needs_client_upload": True,
            "request": request_payload,
        }

    server_path = os.path.abspath(server_path_raw)
    if not os.path.isfile(server_path):
        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "needs_client_upload": True,
            "request": request_payload,
        }

    files_root = os.path.abspath(user_state.files_dir)
    if not server_path.startswith(files_root + os.sep):
        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "text": "The requested file path is outside the server upload storage.",
            "server_path": server_path,
            "unavailable": True,
        }

    try:
        file_size = os.path.getsize(server_path)
        with open(server_path, "rb") as f:
            data = f.read(MAX_REQUESTED_FILE_BYTES + 1)

        byte_truncated = len(data) > MAX_REQUESTED_FILE_BYTES
        if byte_truncated:
            data = data[:MAX_REQUESTED_FILE_BYTES]

        _, text = extract_text_from_bytes(filename, data)
        text = sanitize_prompt_text(text)
        char_truncated = len(text) > MAX_REQUESTED_FILE_CHARS
        if char_truncated:
            text = text[:MAX_REQUESTED_FILE_CHARS].rstrip()

        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "server_path": server_path,
            "text": text,
            "file_size": file_size,
            "byte_truncated": byte_truncated,
            "char_truncated": char_truncated,
        }
    except Exception as e:
        return {
            "key": key,
            "filename": filename,
            "summary": summary,
            "server_path": server_path,
            "text": f"The server has the file, but could not extract readable text from it: {e}",
            "unavailable": True,
        }


def sanitize_prompt_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text.strip()


def with_requested_file_context(
    messages: List[Dict],
    request_response: str,
    requested_file: Dict,
) -> List[Dict]:
    next_messages = list(messages)
    cleaned_response = strip_file_request_markers(request_response).strip()
    if cleaned_response:
        next_messages.append({"role": "assistant", "content": cleaned_response})

    next_messages.append(
        {
            "role": "user",
            "content": build_requested_file_prompt(requested_file),
        }
    )
    return next_messages


def build_requested_file_prompt(requested_file: Dict) -> str:
    flags = []
    if requested_file.get("byte_truncated"):
        flags.append(f"raw bytes were limited to {MAX_REQUESTED_FILE_BYTES} bytes")
    if requested_file.get("char_truncated"):
        flags.append(f"text was limited to {MAX_REQUESTED_FILE_CHARS} characters")
    if requested_file.get("unavailable"):
        flags.append("direct text access is unavailable")

    flag_text = "; ".join(flags) if flags else "complete extracted text was provided"
    return (
        "The server fulfilled your private uploaded-file request. "
        "Use this file information together with the RAG context and answer the user's original question now. "
        "Do not mention the private marker protocol unless the user explicitly asks about implementation details.\n\n"
        f"File key: {requested_file.get('key', '')}\n"
        f"Filename: {requested_file.get('filename', 'uploaded-file')}\n"
        f"Server path: {requested_file.get('server_path', '')}\n"
        f"Summary: {requested_file.get('summary', '')}\n"
        f"Access note: {flag_text}\n\n"
        "----- BEGIN UPLOADED FILE TEXT -----\n"
        f"{requested_file.get('text', '')}\n"
        "----- END UPLOADED FILE TEXT -----"
    )


def build_server_messages(
    messages: List[Dict],
    user_message: str,
    user_state: UserServerState,
) -> tuple:
    context, rag_status = build_server_rag_context(user_message, user_state)
    file_context = build_file_access_context(user_message, user_state)
    memory_context = read_user_memory_for_prompt(user_state)
    augmented = []
    last_user_index = -1
    for index, item in enumerate(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            last_user_index = index

    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            continue
        role = item.get("role", "")
        content = str(item.get("content", ""))
        if index == last_user_index and role == "user":
            augmented.append(
                {
                    "role": role,
                    "content": build_server_prompt(
                        user_message,
                        context,
                        file_context,
                        memory_context,
                    ),
                }
            )
        else:
            augmented.append({"role": role, "content": content})

    if last_user_index < 0 and user_message:
        augmented.append(
            {
                "role": "user",
                "content": build_server_prompt(
                    user_message,
                    context,
                    file_context,
                    memory_context,
                ),
            }
        )

    return augmented, rag_status


def build_server_rag_context(user_message: str, user_state: UserServerState) -> tuple:
    user_state.rag.reload_config()
    diagnostics = rag_config_diagnostics()
    chunk_count = safe_rag_chunk_count(user_state)
    rag_status = {
        "context_used": False,
        "chunk_count": chunk_count,
        "embedding_key_present": diagnostics.get("embedding_api_key_present", False),
        "config_path": diagnostics.get("config_path", ""),
        "reason": "",
    }

    if not diagnostics.get("embedding_api_key_present"):
        rag_status["reason"] = (
            "server embedding API key is missing; "
            f"config path: {diagnostics.get('config_path', '')}"
        )
        if diagnostics.get("config_error"):
            rag_status["reason"] += f"; config error: {diagnostics.get('config_error')}"
        return "", rag_status

    if chunk_count <= 0:
        rag_status["reason"] = "server RAG has no indexed chunks"
        return "", rag_status

    try:
        context = user_state.rag.build_context(user_message)
    except Exception as e:
        rag_status["reason"] = f"server RAG retrieval failed: {e}"
        return "", rag_status

    if not context:
        rag_status["reason"] = "no relevant server RAG chunks matched this message"
        return "", rag_status

    rag_status["context_used"] = True
    rag_status["reason"] = "matched server RAG context"
    return context, rag_status


def safe_rag_chunk_count(user_state: UserServerState) -> int:
    try:
        return user_state.rag.store.count_chunks()
    except Exception:
        return 0


def build_server_prompt(
    user_message: str,
    context: str,
    file_context: str = "",
    memory_context: str = "",
) -> str:
    state_protocol = (
        "AvatarCtrl: on state change, output one standalone line like @@S:thinking@@. "
        "States idle/listening/thinking/searching/coding/explaining/success/warning. "
        "No prose about state. End @@S:idle@@."
    )
    memory_protocol = (
        "AccountMemory: Use only the server-provided account memory for user-specific remembered facts. "
        "If the user asks you to remember a durable preference or fact, include a private standalone "
        "marker @@ANNA_MEMORY:<concise fact>@@. Never mention this marker."
    )
    sections = [state_protocol, memory_protocol]
    if memory_context:
        sections.append(
            "Server-side account memory for this user:\n"
            f"{memory_context}"
        )
    if not context:
        if file_context:
            sections.append(file_context)
        sections.append(f"User message:\n{user_message}")
        return "\n\n".join(sections)

    sections.append(
        "Server-side RAG memory was found for this message. "
        "Use it only if it is relevant; otherwise ignore it.\n\n"
        f"{context}"
    )
    if file_context:
        sections.append(file_context)
    sections.append(f"User message:\n{user_message}")
    return "\n\n".join(sections)


def last_user_message(messages: List[Dict]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            return str(item.get("content", ""))
    return ""


def run_non_stream_chat(messages: List[Dict]) -> str:
    chunks = []

    def on_delta(delta):
        chunks.append(delta)

    if not STATE.gateway.ensure_running():
        raise RuntimeError("Hermes Gateway is not running on this server.")
    STATE.gateway.stream_chat(messages, on_delta, lambda text: None, lambda: None)
    return strip_state_markers(strip_memory_markers(strip_file_request_markers("".join(chunks))))


def cleanup_loop():
    while True:
        try:
            cleanup_storage_if_needed()
        except Exception:
            pass
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def cleanup_storage_if_needed():
    for user_state in STATE.active_user_states():
        if user_state.registry.storage_size() <= STORAGE_LIMIT_BYTES:
            continue

        for record in user_state.registry.list_files():
            if user_state.registry.storage_size() <= STORAGE_LIMIT_BYTES:
                break
            if record.get("status") != "active":
                continue
            if should_delete_file(record):
                delete_server_file(record, user_state)


def should_delete_file(record: Dict) -> bool:
    prompt = (
        f"{SYSTEM_CLEANUP_PHRASE}\n\n"
        "The server file storage is above its size limit. Evaluate this uploaded file's "
        "RAG summary and decide whether it is irrelevant enough to delete the stored file. "
        "The compact summary will be kept even if the file is deleted.\n\n"
        f"File key: {record.get('key', '')}\n"
        f"Filename: {record.get('filename', '')}\n"
        f"Summary: {record.get('summary', '')}\n\n"
        "Reply with exactly one word: KEEP or DELETE."
    )

    try:
        answer = run_non_stream_chat([{"role": "user", "content": prompt}]).strip().upper()
    except Exception:
        return False

    return answer.startswith("DELETE") or "IRRELEVANT" in answer


def delete_server_file(record: Dict, user_state: UserServerState):
    key = record.get("key", "")
    server_path = record.get("server_path", "")
    if server_path and os.path.isfile(server_path):
        os.unlink(server_path)
    if server_path:
        user_state.rag.store.delete_source(os.path.abspath(server_path))

    parent = os.path.dirname(server_path) if server_path else ""
    files_root = os.path.abspath(user_state.files_dir)
    if parent and os.path.abspath(parent).startswith(files_root + os.sep) and os.path.isdir(parent):
        try:
            shutil.rmtree(parent)
        except Exception:
            pass
    user_state.registry.mark_deleted(key, "storage cleanup marked this file irrelevant")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), WebAgentHandler)
    rag_config = rag_config_diagnostics()
    print(f"Anna web agent server listening on http://{HOST}:{PORT}")
    print(f"Admin UI: http://{HOST}:{PORT}/admin")
    print(f"Users: {USERS_DB_PATH}")
    if STATE.bootstrap_admin.get("generated"):
        print(
            "Bootstrap admin created: "
            f"{STATE.bootstrap_admin.get('username')} / {STATE.bootstrap_admin.get('password')}"
        )
        print(f"Bootstrap admin file: {STATE.bootstrap_admin.get('setup_path')}")
    print(f"RAG config: {rag_config.get('config_path')}")
    print(
        "RAG embedding key present: "
        f"{rag_config.get('embedding_api_key_present')} "
        f"(length {rag_config.get('embedding_api_key_length')})"
    )
    if rag_config.get("config_error"):
        print(f"RAG config error: {rag_config.get('config_error')}")
    server.serve_forever()


if __name__ == "__main__":
    main()
