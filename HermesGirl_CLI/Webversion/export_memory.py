import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from typing import Dict, List, Tuple

from hermes_locator import (
    find_hermes_executable,
    find_hermes_home,
    find_hermes_memory_paths,
    is_directory_writable,
    prepare_writable_hermes_home,
)
from rag_ingest import SUMMARY_VERSION, summarize_text
from rag_files import copy_imported_rag_files, unique_file_path
from rag_store import default_rag_db_path


EXPORT_VERSION = 1
MEMORY_DOC_NAME = "hermes_memory_export.md"
BACKUP_DIR_NAME = "Anna Memory Backups"
MEMORY_ARCHIVE_EXTENSION = ".ana"
RAG_FILES_MANIFEST_NAME = "rag_files_manifest.json"


def export_memory_archive(output_dir: str = "", prefix: str = "Anna_Hermes_Memory") -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_name = f"{prefix}_{timestamp}"
    target_dir = output_dir or get_downloads_dir()
    output_archive = os.path.join(target_dir, f"{export_name}{MEMORY_ARCHIVE_EXTENSION}")

    with tempfile.TemporaryDirectory(prefix="anna_memory_export_") as temp_dir:
        package_dir = os.path.join(temp_dir, export_name)
        os.makedirs(package_dir, exist_ok=True)

        memory_doc_path = os.path.join(package_dir, MEMORY_DOC_NAME)
        write_memory_document(memory_doc_path)
        copy_rag_folder(os.path.join(package_dir, "rag"))
        copy_rag_source_files(package_dir)
        write_manifest(package_dir, timestamp)

        zip_directory(package_dir, output_archive)

    return output_archive


def backup_memory_archive(reason: str = "manual") -> str:
    safe_reason = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in reason)
    return export_memory_archive(
        output_dir=get_memory_backup_dir(create=True),
        prefix=f"Anna_Hermes_Backup_{safe_reason}",
    )


def import_memory_archive(archive_path: str) -> Tuple[str, str]:
    if not archive_path or not os.path.isfile(archive_path):
        raise FileNotFoundError("Memory archive was not found.")

    backup_path = backup_memory_archive("before_import")

    with tempfile.TemporaryDirectory(prefix="anna_memory_import_") as temp_dir:
        extract_dir = os.path.join(temp_dir, "archive")
        os.makedirs(extract_dir, exist_ok=True)
        safe_extract_zip(archive_path, extract_dir)

        memory_doc = find_memory_doc(extract_dir)
        if memory_doc:
            imported_memory_path = copy_memory_doc_to_hermes(memory_doc)
            ask_hermes_to_remember(memory_doc)
        else:
            imported_memory_path = ""

        rag_dir = find_rag_dir(extract_dir)
        if rag_dir:
            path_map = restore_rag_source_files(extract_dir)
            restore_rag_folder(rag_dir, path_map)

    return backup_path, imported_memory_path


def restore_memory_backup(archive_path: str) -> Tuple[str, str]:
    return import_memory_archive(archive_path)


def clear_hermes_memory_with_backup() -> str:
    backup_path = backup_memory_archive("before_clear")
    clear_hermes_memory()
    return backup_path


def clear_hermes_memory():
    hermes_home = writable_hermes_home()
    if not hermes_home:
        raise RuntimeError("Could not locate Hermes home.")

    for filename in ["MEMORY.md", "USER.md", "SOUL.md"]:
        path = os.path.join(hermes_home, filename)
        if os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("")

    memories_dir = os.path.join(hermes_home, "memories")
    if os.path.isdir(memories_dir):
        for name in os.listdir(memories_dir):
            path = os.path.join(memories_dir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)


def list_memory_backups() -> List[Dict[str, object]]:
    backup_dir = get_memory_backup_dir(create=True)
    if not os.path.isdir(backup_dir):
        return []

    backups = []
    for filename in os.listdir(backup_dir):
        if not filename.lower().endswith((".ana", ".zip")):
            continue

        path = os.path.join(backup_dir, filename)
        if not os.path.isfile(path):
            continue

        stat = os.stat(path)
        created_at = read_archive_created_at(path) or datetime.fromtimestamp(
            stat.st_mtime
        ).strftime("%Y%m%d_%H%M%S")
        backups.append(
            {
                "name": filename,
                "path": path,
                "created_at": created_at,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
        )

    return sorted(backups, key=lambda item: float(item["mtime"]), reverse=True)


def delete_all_memory_backups() -> int:
    deleted = 0
    for backup in list_memory_backups():
        path = str(backup["path"])
        try:
            os.unlink(path)
            deleted += 1
        except FileNotFoundError:
            pass
    return deleted


def read_archive_created_at(path: str) -> str:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            with zf.open("manifest.json") as f:
                manifest = json.loads(f.read().decode("utf-8"))
    except Exception:
        return ""

    created_at = manifest.get("created_at", "")
    return str(created_at) if created_at else ""


def write_memory_document(path: str):
    memory_files = read_hermes_memory_files()
    hermes_memory_listing = ask_hermes(
        "List the important memories you currently have. Keep it concise, structured, and faithful."
    )
    next_hermes_note = ask_hermes(
        "What memory would you like to leave for the next Hermes? Return concise bullet points only."
    )

    lines = [
        "# Anna / Hermes Memory Export",
        "",
        f"Exported at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Hermes Summary Of Current Memory",
        "",
        hermes_memory_listing or "(Hermes oneshot summary unavailable.)",
        "",
        "## Note For The Next Hermes",
        "",
        next_hermes_note or "(Hermes oneshot note unavailable.)",
        "",
        "## Raw Hermes Memory Files",
        "",
    ]

    if not memory_files:
        lines.append("(No Hermes memory files were found.)")
    else:
        for item in memory_files:
            lines.extend(
                [
                    f"### {item['path']}",
                    "",
                    "```text",
                    item["text"].rstrip(),
                    "```",
                    "",
                ]
            )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def read_hermes_memory_files() -> List[Dict[str, str]]:
    items = []
    for path in find_hermes_memory_paths():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue

        items.append({"path": path, "text": text})

    return items


def ask_hermes(prompt: str, timeout: int = 120) -> str:
    hermes_path = find_hermes_executable()
    if not hermes_path:
        return ""

    env = os.environ.copy()
    env["HERMES_HOME"] = prepare_writable_hermes_home()

    try:
        result = subprocess.run(
            [hermes_path, "-z", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def ask_hermes_to_remember(memory_doc_path: str):
    try:
        with open(memory_doc_path, "r", encoding="utf-8", errors="replace") as f:
            memory_text = f.read()
    except Exception:
        return

    prompt = (
        "这些记忆需要你记住。Integrate the durable user/project facts "
        "into your memory if your memory tools are available. Keep private implementation "
        "details concise.\n\n"
        f"{memory_text[:12000]}"
    )
    ask_hermes(prompt, timeout=180)


def copy_memory_doc_to_hermes(memory_doc_path: str) -> str:
    hermes_home = writable_hermes_home()
    memories_dir = os.path.join(hermes_home, "memories")
    os.makedirs(memories_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(memories_dir, f"anna_imported_memory_{timestamp}.md")
    shutil.copy2(memory_doc_path, target)

    memory_md = os.path.join(hermes_home, "MEMORY.md")
    with open(memory_doc_path, "r", encoding="utf-8", errors="replace") as f:
        imported_text = f.read()

    with open(memory_md, "a", encoding="utf-8") as f:
        f.write("\n\n")
        f.write(f"## Anna Imported Memory {timestamp}\n\n")
        f.write(imported_text.strip())
        f.write("\n")

    return target


def writable_hermes_home() -> str:
    home = find_hermes_home(create=True)
    if home and is_directory_writable(home):
        return home

    return prepare_writable_hermes_home()


def default_rag_dir() -> str:
    return os.path.dirname(default_rag_db_path())


def copy_rag_folder(target_dir: str):
    source_dir = default_rag_dir()
    if not os.path.isdir(source_dir):
        return

    copytree_contents(source_dir, target_dir)


def restore_rag_folder(source_dir: str, source_path_map: Dict[str, str] = None):
    imported_db = os.path.join(source_dir, "rag.sqlite3")
    if os.path.isfile(imported_db):
        merge_rag_database(imported_db, source_path_map or {})
        return

    target_dir = default_rag_dir()
    os.makedirs(target_dir, exist_ok=True)
    copytree_contents(source_dir, target_dir)


def copy_rag_source_files(package_dir: str):
    manifest_items = []
    rag_files_dir = os.path.join(package_dir, "rag_files")
    sources = list_rag_sources()

    for source in sources:
        if not source or not os.path.isfile(source):
            continue

        target_path = unique_file_path(rag_files_dir, os.path.basename(source))
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source, target_path)
        manifest_items.append(
            {
                "source": source,
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


def restore_rag_source_files(extract_dir: str) -> Dict[str, str]:
    source_to_archive_path = read_rag_files_manifest(extract_dir)
    if not source_to_archive_path:
        return {}

    return copy_imported_rag_files(extract_dir, source_to_archive_path)


def read_rag_files_manifest(extract_dir: str) -> Dict[str, str]:
    manifest_path = os.path.join(extract_dir, RAG_FILES_MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return {}

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return {}

    mapping = {}
    for item in manifest.get("files", []):
        source = str(item.get("source", "")).strip()
        archive_path = str(item.get("archive_path", "")).strip()
        if source and archive_path:
            mapping[source] = archive_path
    return mapping


def list_rag_sources() -> List[str]:
    db_path = default_rag_db_path()
    if not os.path.isfile(db_path):
        return []

    try:
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT DISTINCT source FROM chunks")
            return [row[0] for row in cursor.fetchall() if row[0]]
    except Exception:
        return []


def merge_rag_database(imported_db_path: str, source_path_map: Dict[str, str]):
    import sqlite3

    target_db_path = default_rag_db_path()
    os.makedirs(os.path.dirname(target_db_path), exist_ok=True)
    basename_map = {
        os.path.basename(old_source): new_source
        for old_source, new_source in source_path_map.items()
    }

    with sqlite3.connect(imported_db_path) as imported, sqlite3.connect(target_db_path) as target:
        ensure_rag_chunks_table(target)
        imported_columns = get_sqlite_columns(imported, "chunks")
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


def ensure_rag_chunks_table(conn):
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


def copytree_contents(source_dir: str, target_dir: str):
    os.makedirs(target_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        rel = os.path.relpath(root, source_dir)
        dst_root = target_dir if rel == "." else os.path.join(target_dir, rel)
        os.makedirs(dst_root, exist_ok=True)

        for dirname in dirs:
            os.makedirs(os.path.join(dst_root, dirname), exist_ok=True)

        for filename in files:
            if filename.startswith(".DS_Store"):
                continue
            src = os.path.join(root, filename)
            dst = os.path.join(dst_root, filename)
            shutil.copy2(src, dst)


def write_manifest(package_dir: str, timestamp: str):
    manifest = {
        "format": "anna-hermes-memory-export",
        "version": EXPORT_VERSION,
        "created_at": timestamp,
        "memory_document": MEMORY_DOC_NAME,
        "rag_folder": "rag",
        "rag_files_manifest": RAG_FILES_MANIFEST_NAME,
    }
    with open(os.path.join(package_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def zip_directory(source_dir: str, output_zip: str):
    os.makedirs(os.path.dirname(output_zip), exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            for filename in files:
                path = os.path.join(root, filename)
                arcname = os.path.relpath(path, source_dir)
                zf.write(path, arcname)


def safe_extract_zip(archive_path: str, target_dir: str):
    target_dir_abs = os.path.abspath(target_dir)
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            destination = os.path.abspath(os.path.join(target_dir, member.filename))
            if not destination.startswith(target_dir_abs + os.sep) and destination != target_dir_abs:
                raise ValueError("Archive contains an unsafe path.")
        zf.extractall(target_dir)


def find_memory_doc(root_dir: str) -> str:
    for root, _, files in os.walk(root_dir):
        if MEMORY_DOC_NAME in files:
            return os.path.join(root, MEMORY_DOC_NAME)

    for root, _, files in os.walk(root_dir):
        for filename in files:
            if filename.lower().endswith((".md", ".txt")) and "memory" in filename.lower():
                return os.path.join(root, filename)

    return ""


def find_rag_dir(root_dir: str) -> str:
    for root, dirs, _ in os.walk(root_dir):
        if "rag" in dirs:
            return os.path.join(root, "rag")

    return ""


def get_downloads_dir() -> str:
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(downloads):
        return downloads

    return os.path.expanduser("~")


def get_memory_backup_dir(create: bool = True) -> str:
    hermes_home = find_hermes_home(create=create)
    backup_root = hermes_home or get_downloads_dir()
    backup_dir = os.path.join(backup_root, BACKUP_DIR_NAME)
    if create:
        os.makedirs(backup_dir, exist_ok=True)
    return backup_dir
