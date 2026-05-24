import json
import os
import ssl
import urllib.error
import urllib.request
from typing import List, Optional

from rag_settings import load_rag_config

try:
    import certifi
except Exception:
    certifi = None


DEFAULT_EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-VL-Embedding-8B"
DEFAULT_EMBEDDING_DIMENSIONS = ""


class EmbeddingClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        timeout_seconds: int = 30,
    ):
        config = load_rag_config()

        self.api_key = str(
            api_key
            or config.get("embedding_api_key", "")
            or os.environ.get("SILICONFLOW_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or ""
        ).strip()
        self.provider = config.get("embedding_provider", "siliconflow")
        self.api_style = config.get("embedding_api_style", "openai")
        self.base_url = (
            config.get("embedding_base_url")
            or os.environ.get("EMBEDDING_BASE_URL")
            or DEFAULT_EMBEDDING_BASE_URL
        ).rstrip("/")
        self.model = (
            model
            or config.get("embedding_model")
            or os.environ.get("SILICONFLOW_EMBEDDING_MODEL")
            or os.environ.get("OPENAI_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        configured_dimensions = (
            dimensions
            or config.get("embedding_dimensions")
            or os.environ.get("SILICONFLOW_EMBEDDING_DIMENSIONS")
            or os.environ.get("OPENAI_EMBEDDING_DIMENSIONS")
            or DEFAULT_EMBEDDING_DIMENSIONS
        )
        self.dimensions = normalize_dimensions(configured_dimensions)
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = str(
            config.get("embedding_verify_ssl", "true")
        ).lower() not in ["0", "false", "no", "off"]

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key:
            raise RuntimeError("Embedding API key is not set.")

        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_style == "anthropic":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            self.embeddings_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            body = self._open_embedding_request(request)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Embedding request failed: {e.code} {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Embedding request failed: {e}")

        data = json.loads(body)
        embeddings = data.get("data") or data.get("embeddings") or []
        if isinstance(embeddings, list):
            embeddings.sort(key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)

        vectors = [
            item.get("embedding") if isinstance(item, dict) else item
            for item in embeddings
        ]
        if len(vectors) != len(texts) or any(vector is None for vector in vectors):
            raise RuntimeError("Embedding response did not include all vectors.")

        return vectors

    def embed_text(self, text: str) -> List[float]:
        return self.embed_texts([text])[0]

    def embeddings_url(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url

        return f"{self.base_url}/embeddings"

    def _open_embedding_request(self, request: urllib.request.Request) -> str:
        try:
            return open_url(
                request,
                timeout_seconds=self.timeout_seconds,
                verify_ssl=self.verify_ssl,
            )
        except urllib.error.URLError as e:
            if self.verify_ssl and is_certificate_error(e):
                return open_url(
                    request,
                    timeout_seconds=self.timeout_seconds,
                    verify_ssl=False,
                )
            raise


SiliconFlowEmbeddingClient = EmbeddingClient


def normalize_dimensions(value):
    value = str(value or "").strip()
    if not value:
        return None

    dimensions = int(value)
    if dimensions <= 0:
        return None

    return dimensions


def create_ssl_context():
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())

    return ssl.create_default_context()


def create_unverified_ssl_context():
    return ssl._create_unverified_context()


def open_url(
    request: urllib.request.Request,
    timeout_seconds: int,
    verify_ssl: bool = True,
) -> str:
    context = create_ssl_context() if verify_ssl else create_unverified_ssl_context()
    with urllib.request.urlopen(
        request,
        timeout=timeout_seconds,
        context=context,
    ) as response:
        return response.read().decode("utf-8")


def is_certificate_error(error: urllib.error.URLError) -> bool:
    reason = getattr(error, "reason", error)
    return "CERTIFICATE_VERIFY_FAILED" in str(reason)
