import os
import re
from typing import Dict, List

from rag_client import EmbeddingClient
from rag_ingest import clean_text_for_rag, chunk_text, is_probably_garbled_text, summarize_text
from rag_store import RagStore


RAG_RELEVANCE_THRESHOLD = 0.34
RAG_MAX_RESULTS = 2
RAG_SUMMARY_CHARS = 80
RAG_EVIDENCE_CHARS = 220
STATE_PROTOCOL = (
    "AvatarCtrl: on state change, output one standalone line like @@S:thinking@@. "
    "States idle/listening/thinking/searching/coding/explaining/success/warning. "
    "No prose about state. End @@S:idle@@."
)


class RagContextManager:
    def __init__(
        self,
        client: EmbeddingClient = None,
        store: RagStore = None,
    ):
        self.client = client or EmbeddingClient()
        self.store = store or RagStore()
        self.recent_files: Dict[str, str] = {}
        self.last_context_used = False

    def reload_config(self):
        self.client = EmbeddingClient()

    def add_file_text(self, name: str, text: str) -> str:
        safe_name = name or "uploaded-file"
        cleaned_text = clean_text_for_rag(text)
        if not cleaned_text:
            return f"Attached {safe_name}, but no readable text was found."

        self.recent_files[safe_name] = cleaned_text

        if not self.client.is_configured():
            return (
                f"Attached {safe_name}. Add an embedding API key to enable vector search; "
                "the file will still be included directly in the next prompt."
            )

        chunks = chunk_text(cleaned_text, safe_name)
        if not chunks:
            return f"Attached {safe_name}, but no readable text was found."

        vectors = self.client.embed_texts([chunk.summary or chunk.text for chunk in chunks])
        self.store.delete_source(safe_name)
        self.store.add_chunks(chunks, vectors)
        return f"Indexed {safe_name} into {len(chunks)} summarized RAG chunks."

    def build_context(self, query: str, top_k: int = RAG_MAX_RESULTS) -> str:
        sections = []

        if self.client.is_configured() and self.store.count_chunks() > 0:
            query_vector = self.client.embed_text(query)
            results = self.store.search(query_vector, top_k=max(top_k * 10, 16))

            if results:
                lines = ["RAG memory matched this message. Use only if helpful:"]
                for result in results:
                    if len(lines) > top_k:
                        break

                    if is_probably_garbled_text(result["text"]):
                        continue

                    source = result["source"]
                    chunk_index = result["chunk_index"]
                    score = result["score"]
                    if not is_relevant_enough(query, source, score):
                        continue

                    summary = trim_text(
                        result.get("summary") or summarize_text(result["text"]),
                        RAG_SUMMARY_CHARS,
                    )
                    evidence = trim_text(clean_text_for_rag(result["text"]), RAG_EVIDENCE_CHARS)
                    if not summary and not evidence:
                        continue

                    lines.append(
                        "\n"
                        f"[source: {display_source(source)}, chunk: {chunk_index}, score: {score:.3f}]\n"
                        f"summary: {summary}\n"
                        f"evidence: {evidence}"
                    )
                if len(lines) > 1:
                    sections.append("\n".join(lines))

        elif self.recent_files:
            lines = ["Attached file context:"]
            for name, text in list(self.recent_files.items())[-2:]:
                cleaned_text = clean_text_for_rag(text)
                if cleaned_text:
                    lines.append(
                        f"\n[source: {display_source(name)}]\n"
                        f"summary: {trim_text(summarize_text(cleaned_text), RAG_SUMMARY_CHARS)}\n"
                        f"evidence: {trim_text(cleaned_text, RAG_EVIDENCE_CHARS)}"
                    )
            if len(lines) > 1:
                sections.append("\n".join(lines))

        return "\n\n".join(sections).strip()

    def build_augmented_prompt(self, user_message: str) -> str:
        self.last_context_used = False
        context = self.build_context(user_message)
        self.last_context_used = bool(context)
        if not context:
            return f"{STATE_PROTOCOL}\n\nUser message:\n{user_message}"

        return (
            f"{STATE_PROTOCOL}\n\n"
            "A compact RAG memory was found for this message. "
            "Use it only if it is relevant; otherwise ignore it.\n\n"
            f"{context}\n\n"
            "User message:\n"
            f"{user_message}"
        )

    def build_cli_prompt(self, user_message: str) -> str:
        return make_cli_safe_prompt(self.build_augmented_prompt(user_message))


def trim_text(text: str, max_chars: int) -> str:
    text = clean_text_for_rag(text)
    if len(text) <= max_chars:
        return text

    return text[: max_chars - 20].rstrip() + "\n...[trimmed]"


def make_cli_safe_prompt(text: str, max_chars: int = 5000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text or "")
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text

    return text[: max_chars - 24].rstrip() + " ...[context trimmed]"


def is_relevant_enough(query: str, source: str, score: float) -> bool:
    if score >= RAG_RELEVANCE_THRESHOLD:
        return True

    normalized_query = normalize_for_match(query)
    source_name = normalize_for_match(os.path.basename(source or ""))
    source_stem = normalize_for_match(os.path.splitext(os.path.basename(source or ""))[0])
    if source_stem and source_stem in normalized_query and score >= 0.22:
        return True
    if source_name and source_name in normalized_query and score >= 0.22:
        return True

    return False


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def display_source(source: str) -> str:
    return os.path.basename(source or "uploaded-file")
