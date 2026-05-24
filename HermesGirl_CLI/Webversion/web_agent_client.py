import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from web_settings import load_web_config, normalize_server_url, save_web_config


DEFAULT_TIMEOUT = 300


@dataclass
class WebChatResult:
    text: str
    file_request_handled: bool = False


class WebAgentClient:
    def __init__(self, server_url: str = "", api_key: str = ""):
        config = load_web_config()
        self.server_url = normalize_server_url(server_url or config.get("server_url", ""))
        self.api_key = api_key or config.get("api_key", "")
        self.auth_token = config.get("auth_token", "")
        self.username = config.get("username", "")
        self.base_url = f"{self.server_url}/v1"

    def ensure_running(self, timeout: float = 5.0) -> bool:
        return self.health(timeout=timeout)

    def health(self, timeout: float = 2.0) -> bool:
        return bool(self.health_status(timeout=timeout).get("ok"))

    def health_status(self, timeout: float = 2.0) -> Dict:
        try:
            request = urllib.request.Request(
                f"{self.base_url}/health",
                headers=self._headers(),
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body.strip() else {}
                data["ok"] = response.status == 200 and bool(data.get("ok", True))
                return data
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "server_url": self.server_url,
            }

    def start_gateway(self) -> Dict:
        request = urllib.request.Request(
            f"{self.base_url}/gateway/start",
            data=b"{}",
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body.strip() else {}
                data["ok"] = response.status == 200 and bool(data.get("ok", True))
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body) if body.strip() else {}
            except Exception:
                data = {}
            data["ok"] = False
            data["error"] = data.get("error") or f"{e.code} {body}"
            return data
        except Exception as e:
            return {"ok": False, "error": str(e), "server_url": self.server_url}

    def login(self, username: str, password: str) -> Dict:
        payload = {"username": username, "password": password}
        request = urllib.request.Request(
            f"{self.base_url}/auth/login",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body) if body.strip() else {}
            except Exception:
                data = {}
            data["ok"] = False
            data["error"] = data.get("error") or f"{e.code} {body}"
            return data

        token = data.get("token", "")
        user = data.get("user", {}) if isinstance(data.get("user"), dict) else {}
        if token:
            self.auth_token = token
            self.username = user.get("username", username)
            save_web_config(
                {
                    "web_mode_enabled": True,
                    "server_url": self.server_url,
                    "auth_token": token,
                    "username": self.username,
                    "role": user.get("role", ""),
                }
            )
        return data

    def register(self, username: str, password: str) -> Dict:
        payload = {"username": username, "password": password}
        request = urllib.request.Request(
            f"{self.base_url}/auth/register",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body) if body.strip() else {}
            except Exception:
                data = {}
            data["ok"] = False
            data["error"] = data.get("error") or f"{e.code} {body}"
            return data

    def me(self) -> Dict:
        request = urllib.request.Request(
            f"{self.base_url}/auth/me",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body) if body.strip() else {}
            except Exception:
                data = {}
            data["ok"] = False
            data["error"] = data.get("error") or f"{e.code} {body}"
            return data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logout(self) -> Dict:
        try:
            return self._post_json("/auth/logout", {}, timeout=15)
        finally:
            save_web_config({"auth_token": "", "username": "", "role": ""})
            self.auth_token = ""
            self.username = ""

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        on_text_delta: Callable[[str], None],
        on_tool_progress: Callable[[str], None],
        on_done: Callable[[], None],
        on_file_request: Optional[Callable[[Dict], bool]] = None,
    ) -> WebChatResult:
        payload = {
            "model": "hermes-agent",
            "messages": messages,
            "stream": True,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(
                {
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                }
            ),
            method="POST",
        )

        full_text = []
        file_request_handled = False

        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            for event_name, data in parse_sse(response):
                if data == "[DONE]":
                    break

                try:
                    item = json.loads(data)
                except Exception:
                    continue

                if event_name == "anna.file.request":
                    if on_file_request is not None:
                        file_request_handled = bool(on_file_request(item))
                    continue

                if event_name == "hermes.tool.progress":
                    on_tool_progress(format_tool_progress(item))
                    continue

                delta = extract_chat_delta(item)
                if delta:
                    full_text.append(delta)
                    on_text_delta(delta)

        on_done()
        return WebChatResult("".join(full_text), file_request_handled=file_request_handled)

    def upload_file(
        self,
        path: str,
        key: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
        local_path: str = "",
    ) -> Dict:
        path = os.path.abspath(os.path.expanduser(path or ""))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        size = os.path.getsize(path)
        parsed = urllib.parse.urlparse(f"{self.base_url}/files/upload")
        connection = self._connection(parsed)
        upload_path = parsed.path or "/v1/files/upload"
        if parsed.query:
            upload_path = f"{upload_path}?{parsed.query}"

        headers = self._headers(
            {
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
                "X-File-Key": key,
                "X-Filename": urllib.parse.quote(os.path.basename(path), safe=""),
                "X-Local-Path": urllib.parse.quote(local_path or path, safe=""),
            }
        )

        try:
            connection.putrequest("POST", upload_path)
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()

            sent = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 256)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    if on_progress:
                        on_progress(sent, size)

            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if response.status >= 400:
                raise RuntimeError(f"Upload failed: {response.status} {body}")
            return json.loads(body) if body.strip() else {}
        finally:
            connection.close()

    def forget_file(self, key: str) -> Dict:
        payload = {"key": key}
        request = urllib.request.Request(
            f"{self.base_url}/files/forget",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}

    def export_archive(self, output_dir: str, on_progress: Optional[Callable[[int, int], None]] = None) -> str:
        os.makedirs(output_dir, exist_ok=True)
        request = urllib.request.Request(
            f"{self.base_url}/archive/export",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            total = int(response.headers.get("Content-Length", "0") or "0")
            filename = filename_from_content_disposition(
                response.headers.get("Content-Disposition", "")
            )
            if not filename:
                filename = "Anna_Server_Memory.ana"
            path = unique_output_path(output_dir, filename)
            received = 0
            with open(path, "wb") as f:
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, total)
            return path

    def import_archive(
        self,
        path: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict:
        path = os.path.abspath(os.path.expanduser(path or ""))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        return self._post_file(
            endpoint="/archive/import",
            path=path,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": urllib.parse.quote(os.path.basename(path), safe=""),
            },
            on_progress=on_progress,
        )

    def list_backups(self) -> List[Dict]:
        request = urllib.request.Request(
            f"{self.base_url}/backups",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        backups = data.get("backups", [])
        return backups if isinstance(backups, list) else []

    def restore_backup(self, name: str) -> Dict:
        payload = {"name": name}
        return self._post_json("/backups/restore", payload, timeout=DEFAULT_TIMEOUT)

    def delete_all_backups(self) -> Dict:
        return self._post_json("/backups/delete_all", {}, timeout=60)

    def list_files(self) -> List[Dict]:
        request = urllib.request.Request(
            f"{self.base_url}/files",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        files = data.get("files", [])
        return files if isinstance(files, list) else []

    def list_rag_sources(self) -> List[Dict]:
        request = urllib.request.Request(
            f"{self.base_url}/rag/sources",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        sources = data.get("sources", [])
        return sources if isinstance(sources, list) else []

    def get_rag_source(self, source: str) -> Dict:
        encoded = urllib.parse.quote(source or "", safe="")
        request = urllib.request.Request(
            f"{self.base_url}/rag/source?source={encoded}",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}

    def reindex_files(self, key: str = "") -> Dict:
        payload = {"key": key} if key else {}
        return self._post_json("/rag/reindex", payload, timeout=DEFAULT_TIMEOUT)

    def get_memory(self) -> str:
        return self._get_text_endpoint("memory")

    def get_skills(self) -> str:
        return self._get_text_endpoint("skills")

    def _get_text_endpoint(self, name: str) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/{name}",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return str(data.get("text", ""))

    def _post_json(self, endpoint: str, payload: Dict, timeout: int = 30) -> Dict:
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}

    def _post_file(
        self,
        endpoint: str,
        path: str,
        headers: Dict[str, str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict:
        size = os.path.getsize(path)
        parsed = urllib.parse.urlparse(f"{self.base_url}{endpoint}")
        connection = self._connection(parsed)
        request_path = parsed.path or f"/v1{endpoint}"
        if parsed.query:
            request_path = f"{request_path}?{parsed.query}"

        request_headers = self._headers(dict(headers or {}))
        request_headers["Content-Length"] = str(size)

        try:
            connection.putrequest("POST", request_path)
            for name, value in request_headers.items():
                connection.putheader(name, value)
            connection.endheaders()

            sent = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 512)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    if on_progress:
                        on_progress(sent, size)

            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if response.status >= 400:
                raise RuntimeError(f"Server request failed: {response.status} {body}")
            return json.loads(body) if body.strip() else {}
        finally:
            connection.close()

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = dict(extra or {})
        token = self.auth_token or self.api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _connection(self, parsed):
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(
                host,
                port or 443,
                timeout=DEFAULT_TIMEOUT,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(host, port or 80, timeout=DEFAULT_TIMEOUT)


def parse_sse(response) -> Iterable[tuple]:
    event_name = "message"
    data_lines = []

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())

    if data_lines:
        yield event_name, "\n".join(data_lines)


def extract_chat_delta(item: Dict) -> str:
    choices = item.get("choices") or []
    if not choices:
        return ""

    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def format_tool_progress(item: Dict) -> str:
    for key in ["message", "status", "name", "tool", "type"]:
        value = item.get(key)
        if value:
            return str(value)
    return "Hermes is using a tool..."


def filename_from_content_disposition(value: str) -> str:
    value = value or ""
    parts = [part.strip() for part in value.split(";")]
    for part in parts:
        if part.lower().startswith("filename="):
            filename = part.split("=", 1)[1].strip().strip('"')
            return os.path.basename(urllib.parse.unquote(filename))
    return ""


def unique_output_path(directory: str, filename: str) -> str:
    safe_name = os.path.basename(filename or "Anna_Server_Memory.ana")
    stem, ext = os.path.splitext(safe_name)
    candidate = os.path.join(directory, safe_name)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{suffix}{ext}")
        suffix += 1
    return candidate
