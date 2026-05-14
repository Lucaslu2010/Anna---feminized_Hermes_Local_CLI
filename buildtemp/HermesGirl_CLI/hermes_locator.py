import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional


def find_hermes_executable() -> Optional[str]:
    """
    Try to locate the Hermes executable across common macOS install paths.
    """

    candidate_paths = [
        "/opt/homebrew/bin/hermes",
        "/usr/local/bin/hermes",
        os.path.expanduser("~/.local/bin/hermes"),
        os.path.expanduser("~/bin/hermes"),
        os.path.expanduser("~/.cargo/bin/hermes"),
        os.path.expanduser("~/.npm-global/bin/hermes"),
    ]

    # 1. Try current PATH first.
    found = shutil.which("hermes")
    if found:
        return found

    # 2. Try common install locations.
    for path in candidate_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # 3. Try zsh login shell PATH.
    # GUI apps often have a reduced PATH, but a login shell can see the user's normal PATH.
    try:
        result = subprocess.run(
            ["/bin/zsh", "-lc", "command -v hermes"],
            capture_output=True,
            text=True,
            timeout=3,
        )

        path = result.stdout.strip()

        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    except Exception:
        pass

    return None


def build_hermes_command(use_yolo: bool = True) -> List[str]:
    hermes_path = find_hermes_executable()

    if not hermes_path:
        raise FileNotFoundError(
            "Cannot find Hermes executable. Please install Hermes or configure its path."
        )

    command = [hermes_path]

    if use_yolo:
        command.append("--yolo")

    return command


def run_hermes_command(args: List[str], timeout: int = 5) -> str:
    hermes_path = find_hermes_executable()
    if not hermes_path:
        return ""

    try:
        result = subprocess.run(
            [hermes_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def find_hermes_home(create: bool = False) -> str:
    config_path = find_hermes_config_path(create=False)
    if config_path:
        return os.path.dirname(config_path)

    dump = run_hermes_command(["dump"])
    for line in dump.splitlines():
        if "hermes_home:" in line:
            home = line.split("hermes_home:", 1)[1].strip()
            home = os.path.expanduser(home)
            if home:
                return os.path.abspath(home)

    hermes_path = find_hermes_executable()
    if not hermes_path:
        return ""

    default_path = os.path.abspath(os.path.join(os.path.dirname(hermes_path), ".."))
    if create:
        os.makedirs(default_path, exist_ok=True)

    return default_path


def find_hermes_config_path(create: bool = False) -> str:
    path = run_hermes_command(["config", "path"])
    path = os.path.expanduser(path.strip())

    if path:
        if create:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        return os.path.abspath(path)

    return ""


def find_hermes_env_path(create: bool = False) -> str:
    path = run_hermes_command(["config", "env-path"])
    path = os.path.expanduser(path.strip())

    if path:
        if create:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        return os.path.abspath(path)

    return ""


def find_hermes_memory_paths() -> List[str]:
    home = find_hermes_home(create=False)
    if not home:
        return []

    paths = []
    for filename in ["MEMORY.md", "USER.md", "SOUL.md"]:
        path = os.path.join(home, filename)
        if os.path.isfile(path):
            paths.append(path)

    memories_dir = os.path.join(home, "memories")
    if os.path.isdir(memories_dir):
        for root, _, files in os.walk(memories_dir):
            for filename in files:
                if filename.startswith("."):
                    continue
                paths.append(os.path.join(root, filename))

    return sorted(set(paths))


def find_hermes_skill_paths() -> List[str]:
    home = find_hermes_home(create=False)
    if not home:
        return []

    paths = []
    for name in ["skills", "hermes-agent/skills"]:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            paths.append(path)

    return sorted(set(paths))


def get_installed_hermes_skills_text() -> str:
    return run_hermes_command(["skills", "list"], timeout=15)


def describe_hermes_paths(create: bool = False) -> Dict[str, object]:
    return {
        "executable": find_hermes_executable() or "",
        "home": find_hermes_home(create=create),
        "config": find_hermes_config_path(create=create),
        "env": find_hermes_env_path(create=create),
        "memory": find_hermes_memory_paths(),
        "skills": find_hermes_skill_paths(),
    }


def prepare_writable_hermes_home() -> str:
    """
    Return a Hermes home that the GUI subprocess can write to.

    Some sandboxed GUI launches can read ~/.hermes but cannot append history
    or logs there. Hermes crashes after the first message in that situation.
    When the real home is not writable, create a project-local runtime mirror.
    """

    real_home = find_hermes_home(create=False)
    if real_home and is_directory_writable(real_home):
        return real_home

    runtime_home = os.path.join(
        tempfile.gettempdir(),
        "anna_hermesgirl_runtime",
        "hermes_home",
    )
    os.makedirs(runtime_home, exist_ok=True)

    if real_home and os.path.isdir(real_home):
        sync_hermes_runtime_home(real_home, runtime_home)

    os.makedirs(os.path.join(runtime_home, "logs"), exist_ok=True)
    return runtime_home


def is_directory_writable(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False

    try:
        fd, marker = tempfile.mkstemp(prefix=".anna_write_test_", dir=path)
        os.close(fd)
        os.unlink(marker)
        return True
    except Exception:
        return False


def sync_hermes_runtime_home(source_home: str, runtime_home: str):
    file_names = [
        "config.yaml",
        ".env",
        "auth.json",
        "models.json",
        "desktop.json",
        "SOUL.md",
        "USER.md",
        "MEMORY.md",
        "active_profile",
    ]

    dir_names = [
        "skills",
        "memories",
        "plugins",
        "hooks",
        "pairing",
    ]

    for name in file_names:
        src = os.path.join(source_home, name)
        dst = os.path.join(runtime_home, name)
        if os.path.isfile(src):
            copy_if_newer(src, dst)

    for name in dir_names:
        src = os.path.join(source_home, name)
        dst = os.path.join(runtime_home, name)
        if os.path.isdir(src):
            copytree_merge(src, dst)


def copy_if_newer(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    if os.path.exists(dst):
        try:
            if os.path.getmtime(dst) >= os.path.getmtime(src):
                return
        except Exception:
            pass

    shutil.copy2(src, dst)


def copytree_merge(src: str, dst: str):
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)

        dirs[:] = [name for name in dirs if not should_skip_runtime_name(name)]

        for filename in files:
            if should_skip_runtime_name(filename):
                continue

            copy_if_newer(
                os.path.join(root, filename),
                os.path.join(target_root, filename),
            )


def should_skip_runtime_name(name: str) -> bool:
    return (
        name == "__pycache__"
        or name.endswith(".pyc")
        or name.endswith(".lock")
    )
