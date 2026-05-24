import base64
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional


ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hermes_gateway_client import HermesGatewayClient
from rag_context import RagContextManager
from rag_files import copy_file_to_rag_storage
from web_agent_client import WebAgentClient
from web_file_registry import mark_uploaded, remember_local_file
from web_settings import load_web_config, save_web_config
from file_text_extractor import extract_text_from_bytes


HOST = os.environ.get("ANNA_SWIFT_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ANNA_SWIFT_BRIDGE_PORT", "8788"))
BRIDGE_VERSION = "2026-05-24-upload-bytes"
STATE_MARKER_RE = re.compile(
    r"^[^\S\r\n]*@@S:(?:idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@[^\S\r\n]*(?:\r?\n)?"
    r"|@@S:(?:idle|listening|thinking|searching|coding|explain|explaining|success|warning)@@",
    re.IGNORECASE | re.MULTILINE,
)

# Directory access blocking patterns
ABSOLUTE_PATH_RE = re.compile(
    r'(?<![/\w])(/(?:Users|home|etc|var|tmp|opt|root|mnt|media|srv)[/\w.~-]*)',
    re.IGNORECASE,
)
WINDOWS_PATH_RE = re.compile(r'[A-Za-z]:\\[\w\\.~-]+')
PATH_TRAVERSAL_RE = re.compile(r'\.\.[/\\]')
FILE_ACCESS_CMD_RE = re.compile(
    r'\b(?:cat|read|open|access|show|display|print|type|less|more|head|tail|nano|vim|vi|emacs)\s+'
    r'(?:the\s+(?:file|contents?|path|directory)\s+(?:at|of|from|in)?\s*)?[/~]',
    re.IGNORECASE,
)
DIR_NAV_CMD_RE = re.compile(
    r'\b(?:cd|ls|dir|navigate|browse|list|tree|find|locate|du|df)\s+[/~]',
    re.IGNORECASE,
)
OTHER_USER_DIR_RE = re.compile(r'data/users/([^/\s]+)/', re.IGNORECASE)


class BridgeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.local_gateway = None
        self.local_rag = None

    def gateway(self) -> HermesGatewayClient:
        with self.lock:
            if self.local_gateway is None:
                self.local_gateway = HermesGatewayClient()
            return self.local_gateway

    def rag(self) -> RagContextManager:
        with self.lock:
            if self.local_rag is None:
                self.local_rag = RagContextManager()
            return self.local_rag


STATE = BridgeState()

# Location injection state
_location_cache: Dict[str, Dict] = {}
_location_injected: Dict[str, float] = {}
LOCATION_INJECT_TTL = 20 * 60


def check_message_for_directory_access(text: str, username: str) -> tuple:
    """Return (True, reason) if message should be blocked, (False, '') if allowed."""
    uname = (username or "").strip().lower()

    for match in ABSOLUTE_PATH_RE.finditer(text):
        path = match.group(1)
        if uname and (path.lower().startswith(f"/users/{uname}/") or path.lower().startswith(f"/home/{uname}/")):
            continue
        return True, f"Absolute path outside workspace: {path}"

    if WINDOWS_PATH_RE.search(text):
        return True, "Windows-style path detected"

    if PATH_TRAVERSAL_RE.search(text):
        return True, "Path traversal sequence detected"

    if FILE_ACCESS_CMD_RE.search(text):
        return True, "File access command detected"

    if DIR_NAV_CMD_RE.search(text):
        return True, "Directory navigation command detected"

    for match in OTHER_USER_DIR_RE.finditer(text):
        dir_user = match.group(1).strip().lower()
        if not uname or dir_user != uname:
            return True, f"Reference to another user's directory: {dir_user}"

    return False, ""


LOCATION_API_URL = "http://ip-api.com/json"

# CoreLocation delegate (module-level to avoid ObjC redefinition errors)
_cl_location_result: Dict = {}
_cl_location_done = threading.Event()


class _CLLocationDelegate:
    pass


def _init_corelocation_delegate():
    try:
        import Foundation
    except ImportError:
        return False

    global _CLLocationDelegate

    class _LocDelegate(Foundation.NSObject):
        def locationManagerDidChangeAuthorization_(self, mgr):
            auth = mgr.authorizationStatus()
            if auth in (3, 4):
                mgr.requestLocation()
            elif auth != 0:
                _cl_location_done.set()

        def locationManager_didUpdateLocations_(self, mgr, locations):
            if locations and locations.count() > 0:
                loc = locations.lastObject()
                _cl_location_result["lat"] = float(loc.coordinate().latitude)
                _cl_location_result["lon"] = float(loc.coordinate().longitude)
            _cl_location_done.set()

        def locationManager_didFailWithError_(self, mgr, error):
            _cl_location_done.set()

    _CLLocationDelegate = _LocDelegate
    return True


_cl_delegate_initialized = False


def _fetch_location_from_corelocation() -> Optional[Dict]:
    """Use macOS CoreLocation via pyobjc. Requires location permission in System Preferences."""
    global _cl_delegate_initialized
    try:
        import CoreLocation
        import Foundation
    except ImportError:
        return None

    if not _cl_delegate_initialized:
        _cl_delegate_initialized = _init_corelocation_delegate()
        if not _cl_delegate_initialized:
            return None

    _cl_location_result.clear()
    _cl_location_done.clear()

    try:
        mgr = CoreLocation.CLLocationManager.alloc().init()
        delegate = _CLLocationDelegate.alloc().init()
        mgr.setDelegate_(delegate)
        mgr.setDesiredAccuracy_(CoreLocation.kCLLocationAccuracyHundredMeters)

        auth = mgr.authorizationStatus()
        if auth == 0:
            mgr.requestWhenInUseAuthorization()
        elif auth in (3, 4):
            mgr.requestLocation()
        else:
            return None

        for _ in range(15):
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(1.0))
            if _cl_location_done.is_set():
                break

        if "lat" not in _cl_location_result:
            return None

        # Reverse geocode using block callback (not delegate)
        geocoder = CoreLocation.CLGeocoder.alloc().init()
        location = CoreLocation.CLLocation.alloc().initWithLatitude_longitude_(
            _cl_location_result["lat"], _cl_location_result["lon"])

        def _geo_callback(placemarks, error):
            if placemarks and placemarks.count() > 0:
                pm = placemarks.objectAtIndex_(0)
                _cl_location_result["city"] = str(pm.locality() or "")
                _cl_location_result["region"] = str(pm.administrativeArea() or "")
                _cl_location_result["country"] = str(pm.country() or "")

        geocoder.reverseGeocodeLocation_completionHandler_(location, _geo_callback)

        # Geocoder needs run loop spinning, not blocking wait
        for _ in range(6):
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(1.0))
            if "city" in _cl_location_result:
                break

        return dict(_cl_location_result) if _cl_location_result.get("lat") else None
    except Exception:
        return None


def _fetch_location_from_ip() -> Optional[Dict]:
    """Fallback: IP geolocation (affected by VPN)."""
    try:
        req = urllib.request.Request(LOCATION_API_URL, headers={"User-Agent": "AnnaSwiftBridge/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("status") != "success":
            return None
        return {
            "city": data.get("city", ""),
            "region": data.get("regionName", ""),
            "country": data.get("country", ""),
            "lat": data.get("lat", 0.0),
            "lon": data.get("lon", 0.0),
        }
    except Exception:
        return None


def _fetch_location() -> Optional[Dict]:
    """Try CoreLocation first (VPN-proof), fall back to IP geolocation."""
    loc = _fetch_location_from_corelocation()
    if loc:
        return loc
    return _fetch_location_from_ip()


def get_cached_location(username: str) -> Optional[Dict]:
    import time
    now = time.time()
    cached = _location_cache.get(username)
    if cached and (now - cached["fetch_time"]) < LOCATION_INJECT_TTL:
        return cached["location"]
    location = _fetch_location()
    if location is None:
        return cached["location"] if cached else None
    _location_cache[username] = {"location": location, "fetch_time": now}
    return location


def _maybe_inject_location(username: str) -> Optional[Dict]:
    if not username:
        return None
    import time
    now = time.time()
    last_injected = _location_injected.get(username, 0)
    if (now - last_injected) < LOCATION_INJECT_TTL:
        return None
    location = get_cached_location(username)
    if location is None:
        return None
    _location_injected[username] = now
    return _build_location_system_message(location)


def _build_location_system_message(location: Dict) -> Dict:
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    parts = []
    for key in ("city", "region", "country"):
        if location.get(key):
            parts.append(location[key])
    location_str = ", ".join(parts) if parts else "Unknown"
    coords = f"{location.get('lat', 0)}, {location.get('lon', 0)}"
    content = f"Current user location: {location_str}. Coordinates: {coords}. Timestamp: {timestamp}."
    return {"role": "system", "content": content}


def _inject_system_message(messages: List[Dict], system_msg: Dict) -> List[Dict]:
    result = list(messages)
    insert_at = 0
    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            insert_at = i + 1
            break
    result.insert(insert_at, system_msg)
    return result


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "AnnaSwiftBridge/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/config":
            return self.send_json({"ok": True, "config": public_config()})
        if path == "/v1/bridge/version":
            return self.send_json({"ok": True, "version": BRIDGE_VERSION, "port": PORT})
        if path == "/v1/health":
            return self.handle_health()
        if path == "/v1/memory":
            return self.handle_text_endpoint("memory")
        if path == "/v1/skills":
            return self.handle_text_endpoint("skills")
        if path == "/v1/files":
            return self.handle_files()
        if path == "/v1/rag/sources":
            return self.handle_rag_sources()
        if path == "/v1/rag/source":
            return self.handle_rag_source()
        return self.send_json({"ok": False, "error": "not found"}, status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/config":
            return self.handle_config_save()
        if path == "/v1/auth/login":
            return self.handle_login()
        if path == "/v1/auth/register":
            return self.handle_register()
        if path == "/v1/auth/logout":
            return self.handle_logout()
        if path == "/v1/gateway/start":
            return self.handle_gateway_start()
        if path == "/v1/chat":
            return self.handle_chat()
        if path == "/v1/files/upload-path":
            return self.handle_upload_path()
        if path == "/v1/files/upload-bytes":
            return self.handle_upload_bytes()
        if path == "/v1/files/forget":
            return self.handle_file_forget()
        if path == "/v1/rag/reindex":
            return self.handle_rag_reindex()
        return self.send_json({"ok": False, "error": "not found"}, status=404)

    def handle_config_save(self):
        payload = self.read_json()
        config = save_web_config(
            {
                "web_mode_enabled": bool(payload.get("web_mode_enabled")),
                "server_url": payload.get("server_url", ""),
                "location_injection_enabled": bool(payload.get("location_injection_enabled", False)),
            }
        )
        return self.send_json({"ok": True, "path": config, "config": public_config()})

    def handle_login(self):
        payload = self.read_json()
        server_url = str(payload.get("server_url", "")).strip()
        if server_url:
            save_web_config({"server_url": server_url, "web_mode_enabled": True})

        result = WebAgentClient(server_url=server_url).login(
            str(payload.get("username", "")),
            str(payload.get("password", "")),
        )
        return self.send_json(result, status=200 if result.get("ok") else 401)

    def handle_register(self):
        payload = self.read_json()
        server_url = str(payload.get("server_url", "")).strip()
        if server_url:
            save_web_config({"server_url": server_url, "web_mode_enabled": True})

        result = WebAgentClient(server_url=server_url).register(
            str(payload.get("username", "")),
            str(payload.get("password", "")),
        )
        return self.send_json(result, status=202 if result.get("ok") else 400)

    def handle_logout(self):
        try:
            result = WebAgentClient().logout()
        except Exception as e:
            save_web_config({"auth_token": "", "username": ""})
            result = {"ok": True, "warning": str(e)}
        return self.send_json(result)

    def handle_health(self):
        config = load_web_config()
        if config.get("web_mode_enabled"):
            return self.send_json(WebAgentClient().health_status())

        try:
            ready = STATE.gateway().health(timeout=1.0)
            return self.send_json({"ok": True, "mode": "local", "gateway_ready": ready})
        except Exception as e:
            return self.send_json({"ok": False, "mode": "local", "error": str(e)}, status=503)

    def handle_gateway_start(self):
        config = load_web_config()
        if config.get("web_mode_enabled"):
            return self.send_json(WebAgentClient().start_gateway())

        try:
            ready = STATE.gateway().ensure_running()
            return self.send_json({"ok": ready, "mode": "local", "gateway_ready": ready})
        except Exception as e:
            return self.send_json({"ok": False, "mode": "local", "error": str(e)}, status=500)

    def handle_text_endpoint(self, name: str):
        config = load_web_config()
        if not config.get("web_mode_enabled"):
            return self.send_json({"ok": False, "error": f"{name} is only wired for web mode in the Swift bridge."})

        try:
            client = WebAgentClient()
            text = client.get_memory() if name == "memory" else client.get_skills()
            return self.send_json({"ok": True, "text": text})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_files(self):
        config = load_web_config()
        if config.get("web_mode_enabled"):
            try:
                return self.send_json({"ok": True, "files": WebAgentClient().list_files()})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, status=500)

        return self.send_json({"ok": True, "files": local_rag_files()})

    def handle_rag_sources(self):
        config = load_web_config()
        if config.get("web_mode_enabled"):
            try:
                return self.send_json({"ok": True, "sources": WebAgentClient().list_rag_sources()})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, status=500)

        try:
            return self.send_json({"ok": True, "sources": STATE.rag().store.list_sources()})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_rag_source(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        source = urllib.parse.unquote((query.get("source") or [""])[0])
        if not source:
            return self.send_json({"ok": False, "error": "missing source"}, status=400)

        config = load_web_config()
        if config.get("web_mode_enabled"):
            try:
                data = WebAgentClient().get_rag_source(source)
                data["ok"] = True
                return self.send_json(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, status=500)

        try:
            chunks = STATE.rag().store.get_source_chunks(source)
            return self.send_json({"ok": True, "source": source, "chunks": chunks})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_upload_path(self):
        payload = self.read_json()
        path = os.path.abspath(os.path.expanduser(str(payload.get("path", ""))))
        if not os.path.isfile(path):
            return self.send_json({"ok": False, "error": f"file was not found: {path}"}, status=404)

        return self.upload_existing_file(path, str(payload.get("key", "")), path)

    def handle_upload_bytes(self):
        payload = self.read_json()
        filename = os.path.basename(str(payload.get("filename", "")).strip()) or "uploaded-file"
        original_path = str(payload.get("local_path", "")).strip()
        key = str(payload.get("key", "")).strip()
        encoded = str(payload.get("data_base64", "")).strip()
        if not encoded:
            return self.send_json({"ok": False, "error": "empty upload"}, status=400)

        temp_dir = tempfile.mkdtemp(prefix="anna_swift_upload_")
        temp_path = os.path.abspath(os.path.join(temp_dir, filename))
        try:
            with open(temp_path, "wb") as f:
                f.write(base64.b64decode(encoded))
            return self.upload_existing_file(temp_path, key, original_path or temp_path)
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    def upload_existing_file(self, path: str, key: str = "", local_path: str = ""):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            return self.send_json({"ok": False, "error": f"file was not found: {path}"}, status=404)

        config = load_web_config()
        if config.get("web_mode_enabled"):
            try:
                record = remember_local_file(
                    local_path or path,
                    key=key,
                    server_url=config.get("server_url", ""),
                    username=config.get("username", ""),
                )
                key = record.get("key", "") or uuid.uuid4().hex
                result = WebAgentClient().upload_file(path, key, local_path=local_path or path)
                file_record = result.get("file", {}) if isinstance(result, dict) else {}
                mark_uploaded(
                    key,
                    filename=os.path.basename(path),
                    server_url=config.get("server_url", ""),
                    username=config.get("username", ""),
                )
                return self.send_json({"ok": True, "file": file_record})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, status=500)

        try:
            with open(path, "rb") as f:
                _, text = extract_text_from_bytes(path, f.read())
            source = copy_file_to_rag_storage(path)
            status = STATE.rag().add_file_text(source, text)
            return self.send_json(
                {
                    "ok": True,
                    "file": {
                        "key": source,
                        "filename": os.path.basename(path),
                        "server_path": source,
                        "status": "active",
                        "index_status": status,
                        "rag_indexed": status.startswith("Indexed "),
                    },
                }
            )
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_file_forget(self):
        payload = self.read_json()
        key = str(payload.get("key", "")).strip()
        if not key:
            return self.send_json({"ok": False, "error": "missing key"}, status=400)

        config = load_web_config()
        if not config.get("web_mode_enabled"):
            return self.send_json({"ok": False, "error": "deleting local files is not wired in Swift yet"}, status=400)

        try:
            return self.send_json(WebAgentClient().forget_file(key))
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_rag_reindex(self):
        payload = self.read_json()
        key = str(payload.get("key", "")).strip()
        config = load_web_config()
        if not config.get("web_mode_enabled"):
            return self.send_json({"ok": False, "error": "local reindex is not wired in Swift yet"}, status=400)

        try:
            return self.send_json(WebAgentClient().reindex_files(key))
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_chat(self):
        payload = self.read_json()
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        if not messages:
            return self.send_json({"ok": False, "error": "messages is required"}, status=400)

        config = load_web_config()
        username = config.get("username", "")

        if config.get("directory_blocking_enabled", True):
            last_text = last_user_message(messages)
            if last_text:
                blocked, _reason = check_message_for_directory_access(last_text, username)
                if blocked:
                    return self.send_json({"ok": False, "error": "Directory access is not allowed."}, status=403)

        if config.get("location_injection_enabled", True):
            location_msg = _maybe_inject_location(username)
            if location_msg:
                messages = _inject_system_message(messages, location_msg)

        try:
            if config.get("web_mode_enabled"):
                text = run_web_chat(messages)
            else:
                text = run_local_chat(messages)
            return self.send_json({"ok": True, "text": text})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, status=500)

    def read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            data = self.rfile.read(length)
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def send_json(self, payload: Dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def log_message(self, format, *args):
        return


def public_config() -> Dict:
    config = load_web_config()
    return {
        "web_mode_enabled": bool(config.get("web_mode_enabled")),
        "server_url": config.get("server_url", ""),
        "username": config.get("username", ""),
        "role": config.get("role", ""),
        "location_injection_enabled": bool(config.get("location_injection_enabled", False)),
        "signed_in": bool(config.get("auth_token") and config.get("username")),
        "bridge_version": BRIDGE_VERSION,
    }


def run_web_chat(messages: List[Dict]) -> str:
    chunks = []
    client = WebAgentClient()
    client.stream_chat(
        messages,
        on_text_delta=lambda delta: chunks.append(delta),
        on_tool_progress=lambda text: None,
        on_done=lambda: None,
    )
    return strip_state_markers("".join(chunks))


def run_local_chat(messages: List[Dict]) -> str:
    if not STATE.gateway().ensure_running():
        raise RuntimeError("Local Hermes gateway is not running.")

    last = last_user_message(messages)
    prepared = list(messages)
    if last:
        prepared = prepared[:-1] + [{"role": "user", "content": STATE.rag().build_augmented_prompt(last)}]

    chunks = []
    STATE.gateway().stream_chat(
        prepared,
        on_text_delta=lambda delta: chunks.append(delta),
        on_tool_progress=lambda text: None,
        on_done=lambda: None,
    )
    return strip_state_markers("".join(chunks))


def strip_state_markers(text: str) -> str:
    return STATE_MARKER_RE.sub("", text or "").strip()


def local_rag_files() -> List[Dict]:
    files = []
    for source in STATE.rag().store.list_sources():
        source_path = str(source.get("source", ""))
        files.append(
            {
                "key": source_path,
                "filename": os.path.basename(source_path) or source_path,
                "server_path": source_path,
                "status": "active",
                "summary": source.get("summary", ""),
                "rag_indexed": True,
                "index_status": f"{source.get('chunk_count', 0)} chunk(s)",
            }
        )
    return files


def last_user_message(messages: List[Dict]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            return str(item.get("content", ""))
    return ""


def main():
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    print(f"Anna Swift bridge {BRIDGE_VERSION} listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
