import base64
import os
import re
import zipfile
from io import BytesIO
from typing import Tuple
from xml.etree import ElementTree


def extract_text_from_data_url(name: str, data_url: str) -> Tuple[str, str]:
    if "," in data_url:
        _, encoded = data_url.split(",", 1)
    else:
        encoded = data_url

    data = base64.b64decode(encoded)
    return extract_text_from_bytes(name, data)


def extract_text_from_bytes(name: str, data: bytes) -> Tuple[str, str]:
    extension = os.path.splitext(name.lower())[1]

    if extension == ".docx":
        return name, extract_docx_text(data)

    if extension in [".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".yaml", ".yml"]:
        return name, decode_text_bytes(data)

    text = decode_text_bytes(data)
    if is_probably_binary_text(text):
        raise ValueError(f"{name} does not look like a readable text file.")

    return name, text


def extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as docx:
        xml = docx.read("word/document.xml")

    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = []
        for node in paragraph.findall(".//w:t", namespace):
            if node.text:
                parts.append(node.text)
        if parts:
            paragraphs.append("".join(parts))

    return "\n".join(paragraphs).strip()


def decode_text_bytes(data: bytes) -> str:
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "latin-1"]:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def is_probably_binary_text(text: str) -> bool:
    if not text:
        return True

    sample = text[:2000]
    control_chars = re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", sample)
    replacement_count = sample.count("\ufffd")

    return (len(control_chars) + replacement_count) / max(len(sample), 1) > 0.02
