import os
import shutil
import subprocess
from typing import List, Optional


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