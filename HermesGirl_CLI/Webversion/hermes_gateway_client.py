import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional

from hermes_locator import (
    find_hermes_env_path,
    find_hermes_executable,
    prepare_writable_hermes_home,
)


DEFAULT_API_KEY = "anna-local-dev"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "8642"
DEFAULT_GATEWAY_LOG = os.path.join(os.path.dirname(__file__), "server", "data", "hermes_gateway.log")
WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}


class HermesGatewayClient:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.config = ensure_gateway_env()
        self.bind_host = self.config.get("API_SERVER_HOST", DEFAULT_HOST) or DEFAULT_HOST
        self.port = self.config.get("API_SERVER_PORT", DEFAULT_PORT) or DEFAULT_PORT
        self.connect_host = os.environ.get(
            "ANNA_HERMES_GATEWAY_CONNECT_HOST",
            connectable_host(self.bind_host),
        ) or connectable_host(self.bind_host)
        self.base_url = (
            f"http://{url_host(self.connect_host)}:"
            f"{self.port}/v1"
        )
        self.api_key = self.config.get("API_SERVER_KEY", DEFAULT_API_KEY)
        self.last_error = ""
        self.last_health_error = ""

    def health(self, timeout: float = 1.5) -> bool:
        try:
            request = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status == 200
        except Exception as e:
            self.last_health_error = str(e)
            return False

    def ensure_running(self, timeout: float = 10.0) -> bool:
        if self.health():
            self.last_error = ""
            return True

        hermes_path = find_hermes_executable()
        if not hermes_path:
            self.last_error = "Cannot find Hermes executable."
            raise RuntimeError("Cannot find Hermes executable.")

        env = os.environ.copy()
        env["HERMES_HOME"] = prepare_writable_hermes_home()
        env["API_SERVER_ENABLED"] = "true"
        env["API_SERVER_KEY"] = self.api_key
        env["API_SERVER_HOST"] = self.bind_host
        env["API_SERVER_PORT"] = self.port

        log_path = os.environ.get("ANNA_WEB_GATEWAY_LOG", DEFAULT_GATEWAY_LOG)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_file = open(log_path, "ab")
        log_file.write(
            (
                "\n\n--- starting hermes gateway ---\n"
                f"command: {hermes_path} gateway\n"
                f"bind: {self.bind_host}:{self.port}\n"
                f"health: {self.base_url}/health\n"
            ).encode("utf-8", errors="replace")
        )
        log_file.flush()

        try:
            self.process = subprocess.Popen(
                [hermes_path, "gateway"],
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                env=env,
                close_fds=True,
            )
        except Exception as e:
            log_file.close()
            self.last_error = f"Failed to start Hermes gateway: {e}"
            raise

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health(timeout=1.0):
                log_file.close()
                self.last_error = ""
                return True
            if self.process and self.process.poll() is not None:
                self.last_error = (
                    f"Hermes gateway exited early with code {self.process.returncode}. "
                    f"See log: {log_path}{format_log_tail(log_path)}"
                )
                log_file.close()
                return False
            time.sleep(0.25)

        health_error = getattr(self, "last_health_error", "")
        detail = f" Last health error: {health_error}." if health_error else ""
        self.last_error = (
            f"Hermes gateway did not become ready within {timeout:.0f}s at {self.base_url}."
            f"{detail} See log: {log_path}{format_log_tail(log_path)}"
        )
        log_file.close()
        return False

    def stop_process(self):
        if not self.process:
            return

        try:
            self.process.terminate()
            self.process.wait(timeout=2)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        on_text_delta: Callable[[str], None],
        on_tool_progress: Callable[[str], None],
        on_done: Callable[[], None],
    ) -> str:
        payload = {
            "model": "hermes-agent",
            "messages": messages,
            "stream": True,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        full_text = []
        with urllib.request.urlopen(request, timeout=300) as response:
            for event_name, data in parse_sse(response):
                if data == "[DONE]":
                    break

                try:
                    item = json.loads(data)
                except Exception:
                    continue

                if event_name == "hermes.tool.progress":
                    on_tool_progress(format_tool_progress(item))
                    continue

                delta = extract_chat_delta(item)
                if delta:
                    full_text.append(delta)
                    on_text_delta(delta)

        on_done()
        return "".join(full_text)


def ensure_gateway_env() -> Dict[str, str]:
    path = find_hermes_env_path(create=True)
    if not path:
        path = os.path.expanduser("~/.hermes/.env")
        os.makedirs(os.path.dirname(path), exist_ok=True)

    values, lines = read_env_file(path)
    changed = False

    defaults = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": DEFAULT_HOST,
        "API_SERVER_PORT": DEFAULT_PORT,
        "API_SERVER_KEY": values.get("API_SERVER_KEY") or DEFAULT_API_KEY,
    }

    for key, value in defaults.items():
        if not values.get(key):
            values[key] = value
            changed = True

    if values.get("API_SERVER_ENABLED", "").lower() != "true":
        values["API_SERVER_ENABLED"] = "true"
        changed = True

    if changed:
        write_env_file(path, values, lines)

    return values


def connectable_host(host: str) -> str:
    host = (host or "").strip()
    if host in WILDCARD_HOSTS:
        return DEFAULT_HOST
    return host


def url_host(host: str) -> str:
    host = (host or DEFAULT_HOST).strip()
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


def format_log_tail(path: str, max_bytes: int = 3000) -> str:
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            return ""
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - max_bytes))
            text = f.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""

    if not text:
        return ""
    return f"\nRecent gateway log:\n{text}"


def read_env_file(path: str):
    values = {}
    lines = []

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values, lines


def write_env_file(path: str, values: Dict[str, str], original_lines: Iterable[str]):
    remaining = dict(values)
    output = []

    for line in original_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={shell_quote_env(remaining.pop(key))}\n")
        else:
            output.append(line)

    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"

    for key, value in remaining.items():
        output.append(f"{key}={shell_quote_env(value)}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(output)


def shell_quote_env(value: str) -> str:
    value = str(value)
    if not value or any(ch.isspace() for ch in value):
        return json.dumps(value)
    return value


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
