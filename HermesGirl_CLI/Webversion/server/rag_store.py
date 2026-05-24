import json
import math
import os
import sqlite3
from typing import Dict, List

from rag_ingest import SUMMARY_VERSION, summarize_text


def default_rag_db_path() -> str:
    root = os.path.join(os.path.expanduser("~"), ".anna_rag")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "rag.sqlite3")


class RagStore:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or default_rag_db_path()
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
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
            self._ensure_column(conn, "chunks", "summary", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                conn,
                "chunks",
                "summary_version",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._refresh_outdated_summaries(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)"
            )

    def _ensure_column(self, conn, table: str, column: str, definition: str):
        cursor = conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _refresh_outdated_summaries(self, conn):
        cursor = conn.execute(
            """
            SELECT id, text
            FROM chunks
            WHERE summary_version < ? OR summary IS NULL OR summary = ''
            """,
            (SUMMARY_VERSION,),
        )
        rows = cursor.fetchall()
        if not rows:
            return

        conn.executemany(
            "UPDATE chunks SET summary = ?, summary_version = ? WHERE id = ?",
            [(summarize_text(text), SUMMARY_VERSION, item_id) for item_id, text in rows],
        )

    def delete_source(self, source: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE source = ?", (source,))

    def delete_all(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks")

    def delete_garbled_chunks(self) -> int:
        from rag_ingest import is_probably_garbled_text

        with self._connect() as conn:
            cursor = conn.execute("SELECT id, text FROM chunks")
            ids = [
                row[0]
                for row in cursor.fetchall()
                if is_probably_garbled_text(row[1])
            ]

            if not ids:
                return 0

            conn.executemany("DELETE FROM chunks WHERE id = ?", [(item_id,) for item_id in ids])
            return len(ids)

    def add_chunks(self, chunks, vectors: List[List[float]]):
        with self._connect() as conn:
            for chunk, vector in zip(chunks, vectors):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chunks
                        (source, chunk_index, text, summary, summary_version, vector_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.source,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.summary,
                        SUMMARY_VERSION,
                        json.dumps(vector),
                    ),
                )

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict]:
        rows = []
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT source, chunk_index, text, summary, vector_json FROM chunks"
            )
            rows = cursor.fetchall()

        scored = []
        for source, chunk_index, text, summary, vector_json in rows:
            vector = json.loads(vector_json)
            score = cosine_similarity(query_vector, vector)
            scored.append(
                {
                    "source": source,
                    "chunk_index": chunk_index,
                    "text": text,
                    "summary": summary,
                    "score": score,
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def count_chunks(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM chunks")
            return int(cursor.fetchone()[0])

    def list_sources(self) -> List[Dict]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT source, COUNT(*) AS chunk_count, MAX(created_at) AS updated_at
                FROM chunks
                GROUP BY source
                ORDER BY updated_at DESC, source ASC
                """
            )
            return [
                {
                    "source": row[0],
                    "chunk_count": row[1],
                    "updated_at": row[2],
                }
                for row in cursor.fetchall()
            ]

    def get_source_chunks(self, source: str) -> List[Dict]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT chunk_index, text, summary, created_at
                FROM chunks
                WHERE source = ?
                ORDER BY chunk_index ASC
                """,
                (source,),
            )
            return [
                {
                    "chunk_index": row[0],
                    "text": row[1],
                    "summary": row[2],
                    "created_at": row[3],
                }
                for row in cursor.fetchall()
            ]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)
