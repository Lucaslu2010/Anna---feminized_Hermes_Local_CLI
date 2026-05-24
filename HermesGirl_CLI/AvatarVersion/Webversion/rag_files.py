import os
import shutil
from typing import Dict

from hermes_locator import find_hermes_home


RAG_FILES_DIR_NAME = "Anna RAG Files"


def get_rag_files_dir(create: bool = True) -> str:
    hermes_home = find_hermes_home(create=False) or os.path.expanduser("~/.hermes")
    hermes_home = os.path.abspath(os.path.expanduser(hermes_home))
    if create:
        os.makedirs(hermes_home, exist_ok=True)

    files_dir = os.path.join(hermes_home, RAG_FILES_DIR_NAME)
    if create:
        os.makedirs(files_dir, exist_ok=True)
    return files_dir


def copy_file_to_rag_storage(source_path: str) -> str:
    source_path = os.path.abspath(os.path.expanduser(source_path))
    target_dir = get_rag_files_dir(create=True)

    if is_path_inside(source_path, target_dir):
        return source_path

    target_path = unique_file_path(target_dir, os.path.basename(source_path))
    shutil.copy2(source_path, target_path)
    return target_path


def copy_imported_rag_files(source_dir: str, source_to_archive_path: Dict[str, str]) -> Dict[str, str]:
    path_map = {}
    target_dir = get_rag_files_dir(create=True)

    for original_source, archive_path in source_to_archive_path.items():
        archive_file = os.path.join(source_dir, archive_path)
        if not os.path.isfile(archive_file):
            continue

        target_path = unique_file_path(target_dir, os.path.basename(archive_file))
        shutil.copy2(archive_file, target_path)
        path_map[original_source] = target_path

    return path_map


def unique_file_path(directory: str, filename: str) -> str:
    safe_name = os.path.basename(filename or "rag_file")
    stem, ext = os.path.splitext(safe_name)
    stem = stem or "rag_file"

    candidate = os.path.join(directory, f"{stem}{ext}")
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{suffix}{ext}")
        suffix += 1

    return candidate


def is_path_inside(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(directory)]) == os.path.abspath(directory)
    except ValueError:
        return False


def delete_rag_storage_file(path: str) -> bool:
    files_dir = get_rag_files_dir(create=True)
    path = os.path.abspath(os.path.expanduser(path or ""))
    if not path or not is_path_inside(path, files_dir) or not os.path.isfile(path):
        return False

    os.unlink(path)
    return True


def clear_rag_storage_files() -> int:
    files_dir = get_rag_files_dir(create=True)
    deleted = 0
    for name in os.listdir(files_dir):
        path = os.path.join(files_dir, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
            deleted += 1
        elif os.path.exists(path):
            os.unlink(path)
            deleted += 1
    return deleted
