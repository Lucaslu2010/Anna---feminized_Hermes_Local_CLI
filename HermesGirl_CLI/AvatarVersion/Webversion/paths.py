import os
import sys


def resource_path(relative_path: str) -> str:
    """
    Return absolute path to a resource.

    Works in both:
    1. normal Python development mode
    2. PyInstaller bundled app mode
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)