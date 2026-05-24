import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import List


@dataclass
class TextChunk:
    source: str
    chunk_index: int
    text: str
    summary: str


SUMMARY_VERSION = 3
ENGLISH_STOPWORDS = {
    "about",
    "above",
    "after",
    "affect",
    "affects",
    "again",
    "against",
    "all",
    "also",
    "although",
    "always",
    "am",
    "among",
    "an",
    "any",
    "are",
    "around",
    "as",
    "at",
    "be",
    "basic",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "done",
    "during",
    "each",
    "few",
    "first",
    "for",
    "from",
    "had",
    "has",
    "have",
    "having",
    "here",
    "how",
    "if",
    "in",
    "is",
    "it",
    "its",
    "into",
    "just",
    "may",
    "might",
    "more",
    "most",
    "much",
    "must",
    "not",
    "now",
    "of",
    "on",
    "only",
    "or",
    "other",
    "our",
    "out",
    "over",
    "own",
    "same",
    "second",
    "shall",
    "should",
    "simple",
    "since",
    "so",
    "some",
    "such",
    "that",
    "the",
    "their",
    "there",
    "these",
    "third",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "up",
    "very",
    "was",
    "we",
    "when",
    "what",
    "where",
    "which",
    "while",
    "with",
    "would",
    "and",
    "formula",
    "makes",
}
CJK_PREFIX_WORDS = ["首先", "其次", "再次", "最后", "第一", "第二", "第三", "此外", "同时"]
CJK_WEAK_CHARS = set("的一是在和了有为也就都而及与或对这那其等于以之")


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

    keywords = extract_keywords(text, max_keywords=max_words)
    if keywords:
        return trim_to_chars(", ".join(keywords), max_chars)

    sentences = split_sentences(text)
    if not sentences:
        return trim_to_compact_label(text, max_chars, max_words)
    return trim_to_compact_label(sentences[0], max_chars, max_words)


def extract_keywords(text: str, max_keywords: int = 8) -> List[str]:
    candidates = []
    candidates.extend(extract_latin_keyword_phrases(text))
    candidates.extend(extract_cjk_keyword_phrases(text))
    candidates.extend(extract_number_terms(text))

    if not candidates:
        return []

    counts = Counter()
    first_seen = {}
    for index, candidate in enumerate(candidates):
        normalized = normalize_keyword(candidate)
        if not is_good_keyword(normalized):
            continue
        counts[normalized] += keyword_weight(normalized)
        first_seen.setdefault(normalized, index)

    ranked = sorted(
        counts,
        key=lambda item: (-counts[item], first_seen[item], -len(item)),
    )
    return select_distinct_keywords(ranked, max_keywords)


def select_distinct_keywords(ranked: List[str], max_keywords: int) -> List[str]:
    selected = []
    for keyword in ranked:
        if is_redundant_keyword(keyword, selected):
            continue
        selected.append(keyword)
        if len(selected) >= max_keywords:
            break
    return selected


def is_redundant_keyword(keyword: str, selected: List[str]) -> bool:
    keyword_tokens = keyword_token_set(keyword)
    for existing in selected:
        existing_tokens = keyword_token_set(existing)
        if not keyword_tokens or not existing_tokens:
            continue
        if keyword_tokens <= existing_tokens:
            return True
        if existing_tokens <= keyword_tokens and len(existing_tokens) >= 2:
            return True
        overlap = len(keyword_tokens & existing_tokens) / max(len(keyword_tokens), 1)
        if overlap >= 0.75:
            return True
    return False


def keyword_token_set(keyword: str):
    if re.search(r"[A-Za-z]", keyword):
        return {
            token
            for token in re.findall(r"[a-z0-9]+", keyword.lower())
            if token not in ENGLISH_STOPWORDS
        }
    return set(keyword)


def extract_latin_keyword_phrases(text: str) -> List[str]:
    phrases = []
    token_re = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)?")
    clauses = []
    for sentence in split_sentences(text[:6000]) or [text[:6000]]:
        clauses.extend(re.split(r"[,;:()\[\]]+", sentence))

    for clause in clauses:
        words = token_re.findall(clause)
        current = []
        for word in words:
            lowered = word.lower().strip("-'")
            if lowered in ENGLISH_STOPWORDS or len(lowered) < 3:
                if current:
                    phrases.extend(join_keyword_windows(current))
                    current = []
                continue
            current.append(lowered)
            if len(current) >= 4:
                phrases.extend(join_keyword_windows(current))
                current = current[-1:]
        if current:
            phrases.extend(join_keyword_windows(current))
    return phrases


def join_keyword_windows(words: List[str]) -> List[str]:
    if not words:
        return []
    if len(words) == 1:
        return words

    phrases = []
    for size in range(min(3, len(words)), 0, -1):
        for index in range(0, len(words) - size + 1):
            phrases.append(" ".join(words[index : index + size]))
    return phrases


def extract_cjk_keyword_phrases(text: str) -> List[str]:
    phrases = []
    clauses = re.split(r"[，。！？；、：:,.!?;()\[\]【】\s]+", text[:6000])
    for clause in clauses:
        blocks = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", clause)
        for block in split_cjk_connectors("".join(blocks)):
            if len(block) < 2:
                continue

            cleaned = clean_cjk_phrase(block)
            if len(cleaned) < 2:
                continue

            if len(cleaned) <= 10:
                phrases.append(cleaned)
                continue

            chunks = split_long_cjk_phrase(cleaned)
            phrases.extend(chunks)
    return phrases


def split_cjk_connectors(text: str) -> List[str]:
    if not text:
        return []
    return [part for part in re.split(r"(?:和|与|及|以及|都会|都能|都可以)", text) if part]


def clean_cjk_phrase(phrase: str) -> str:
    phrase = phrase or ""
    for prefix in CJK_PREFIX_WORDS:
        if phrase.startswith(prefix):
            phrase = phrase[len(prefix) :]
            break

    phrase = phrase.replace("的", "")
    return phrase.strip()


def split_long_cjk_phrase(phrase: str) -> List[str]:
    chunks = []
    pattern = re.compile(
        r"[\u3400-\u4dbf\u4e00-\u9fff]{2,8}?"
        r"(?:人口|因素|影响|变化|下降|上升|增加|减少|老龄化|生育率|死亡率|出生率|迁移|成本|政策|结构|婚育|教育|住房)"
    )
    for match in pattern.findall(phrase):
        cleaned = trim_weak_cjk_edges(match)
        if 2 <= len(cleaned) <= 12:
            chunks.append(cleaned)

    if chunks:
        return chunks

    return [phrase[index : index + 8] for index in range(0, len(phrase), 8) if len(phrase[index : index + 8]) >= 2]


def trim_weak_cjk_edges(text: str) -> str:
    while text and text[0] in CJK_WEAK_CHARS:
        text = text[1:]
    while text and text[-1] in CJK_WEAK_CHARS:
        text = text[:-1]
    return text


def extract_number_terms(text: str) -> List[str]:
    return re.findall(
        r"\b(?:\d+(?:\.\d+)?%?|\d{4})(?:\s+[A-Za-z][A-Za-z0-9-]{2,}){0,2}",
        text[:6000],
    )


def normalize_keyword(keyword: str) -> str:
    keyword = re.sub(r"\s+", " ", keyword or "").strip(" ,.;:!?()[]{}\"'")
    if not keyword:
        return ""

    if re.search(r"[A-Za-z]", keyword):
        tokens = []
        previous = ""
        for token in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", keyword.lower()):
            token = token.strip("-'")
            if not token or token in ENGLISH_STOPWORDS or token == previous:
                continue
            tokens.append(token)
            previous = token
        return " ".join(tokens)

    return keyword


def is_good_keyword(keyword: str) -> bool:
    if not keyword:
        return False
    if len(keyword) < 2:
        return False
    if keyword in ENGLISH_STOPWORDS:
        return False
    if re.search(r"[A-Za-z]", keyword):
        tokens = keyword.split()
        if not tokens:
            return False
        if any(token in ENGLISH_STOPWORDS for token in tokens):
            return False
        if len(tokens) == 1 and len(tokens[0]) < 4:
            return False
    if re.fullmatch(r"\d+", keyword) and len(keyword) < 4:
        return False
    return True


def keyword_weight(keyword: str) -> int:
    if " " in keyword:
        return 2 + min(len(keyword.split()), 3)
    if any(is_cjk_char(ch) for ch in keyword):
        return 2 + min(len(keyword), 6)
    return 1


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
