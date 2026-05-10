import re
import unicodedata
from dataclasses import dataclass
from typing import List


@dataclass
class TextChunk:
    source: str
    chunk_index: int
    text: str
    summary: str


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text.strip()


def is_probably_garbled_text(text: str) -> bool:
    text = normalize_text(text)
    if not text:
        return True

    sample = text[:3000]
    replacement_ratio = sample.count("\ufffd") / max(len(sample), 1)
    control_ratio = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", sample)) / max(
        len(sample), 1
    )
    marker_hits = sum(
        marker in sample
        for marker in [
            "PK\x03\x04",
            "[Content_Types].xml",
            "word/theme/",
            "word/theme/theme1.xml",
            "not a controlling terminal",
        ]
    )

    letters_or_numbers = 0
    separators = 0
    symbols = 0
    private_or_unknown = 0
    for ch in sample:
        category = unicodedata.category(ch)
        if category[0] in ["L", "N"]:
            letters_or_numbers += 1
        elif category[0] in ["Z", "P"]:
            separators += 1
        elif category[0] == "S":
            symbols += 1
        elif category[0] == "C" and ch not in "\n\t":
            private_or_unknown += 1

    length = max(len(sample), 1)
    textish_ratio = (letters_or_numbers + separators) / length
    noisy_ratio = (symbols + private_or_unknown) / length

    if replacement_ratio > 0.005 or control_ratio > 0.01:
        return True

    if len(sample) < 120:
        return bool(marker_hits)

    if marker_hits and (noisy_ratio > 0.08 or textish_ratio < 0.70):
        return True
    if noisy_ratio > 0.35 and textish_ratio < 0.60:
        return True

    return False


def clean_text_for_rag(text: str) -> str:
    text = normalize_text(text)
    if not text:
        return ""

    cleaned_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        if is_probably_garbled_text(line):
            continue

        line = line.replace("\ufffd", "")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if is_probably_garbled_text(cleaned):
        return ""

    return cleaned


def chunk_text(
    text: str,
    source: str,
    chunk_chars: int = 2400,
    overlap_chars: int = 300,
) -> List[TextChunk]:
    text = clean_text_for_rag(text)
    if not text:
        return []

    overlap_chars = max(0, min(overlap_chars, max(chunk_chars // 3, 0)))

    paragraphs = re.split(r"\n{2,}", text)
    chunks = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)

        while len(paragraph) > chunk_chars:
            chunks.append(paragraph[:chunk_chars])
            paragraph = paragraph[chunk_chars - overlap_chars :]

        current = paragraph

    if current:
        chunks.append(current)

    text_chunks = []
    previous_tail = ""
    for index, chunk in enumerate(chunks):
        if previous_tail and index > 0:
            chunk = f"{previous_tail}\n{chunk}"

        text_chunks.append(
            TextChunk(
                source=source,
                chunk_index=index,
                text=chunk,
                summary=summarize_text(chunk),
            )
        )
        previous_tail = chunk[-overlap_chars:] if overlap_chars > 0 else ""

    return text_chunks


def summarize_text(text: str, max_chars: int = 80, max_words: int = 8) -> str:
    text = clean_text_for_rag(text)
    if not text:
        return ""

    sentences = split_sentences(text)
    if not sentences:
        return trim_to_compact_label(text, max_chars, max_words)

    return trim_to_compact_label(sentences[0], max_chars, max_words)


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    parts = re.split(r"(?<=[。！？.!?؛؟])\s+", text)
    if len(parts) == 1:
        parts = re.split(r"(?<=[。！？.!?؛؟])", text)

    return [part.strip() for part in parts if part.strip()]


def trim_to_chars(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text

    return text[: max_chars - 14].rstrip() + "...[trimmed]"


def trim_to_compact_label(text: str, max_chars: int, max_words: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""

    words = re.findall(r"\S+", text)
    if len(words) > 1:
        label = " ".join(words[:max_words])
        return trim_to_chars(label, max_chars)

    cjk_count = sum(1 for ch in text if is_cjk_char(ch))
    if cjk_count >= max(2, len(text) // 3):
        return trim_to_chars(text, min(max_chars, 28))

    return trim_to_chars(text, max_chars)


def is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )
