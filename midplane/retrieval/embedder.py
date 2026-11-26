"""Embedders. The default is local and deterministic. Adapters are optional.

The hashed embedder is what tests and the default process use. It does not
download weights and it does not open a socket. Sentence-transformers and
OpenAI-compatible HTTP adapters are constructed only when a caller asks for
them. Neither is imported by the default path.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from typing import Protocol, Sequence

from midplane.retrieval.text import tokens

# Fixed so the pgvector column in migrations/004_retrieval.sql can name it.
# A different model dimension needs a new migration. See docs/architecture/retrieval.md.
EMBEDDING_DIM = 256


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit vector per text."""

    def embed_query(self, text: str) -> list[float]:
        """Embed one query with the same space as ``embed``."""


def _bucket(gram: str, dimension: int) -> int:
    digest = hashlib.sha256(gram.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dimension


def _grams(text: str) -> list[str]:
    words = tokens(text)
    grams = list(words)
    grams.extend(f"{left}_{right}" for left, right in zip(words, words[1:]))
    compact = "".join(character for character in text.lower() if character.isalnum())
    if len(compact) >= 3:
        grams.extend(compact[index : index + 3] for index in range(len(compact) - 2))
    return grams


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return values
    return [value / norm for value in values]


class HashedNgramEmbedder:
    """Hashed word and character n-gram TF-IDF, projected to a fixed dimension.

    ``fit`` learns document frequencies. Without it, every hashed gram uses
    the same smoothed IDF, which is sublinear TF. Vectors are L2-normalized
    so a dot product is cosine similarity. Hashing uses SHA-256, not Python's
    salted ``hash``, so the vectors match across processes.
    """

    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        if dimension < 8:
            raise ValueError("dimension must be at least 8")
        self.dimension = dimension
        self._df: dict[int, int] = {}
        self._docs = 0

    def fit(self, texts: Sequence[str]) -> None:
        counts: dict[int, int] = {}
        for text in texts:
            seen: set[int] = set()
            for gram in _grams(text):
                bucket = _bucket(gram, self.dimension)
                if bucket in seen:
                    continue
                seen.add(bucket)
                counts[bucket] = counts.get(bucket, 0) + 1
        self._df = counts
        self._docs = len(texts)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        counts: dict[int, int] = {}
        for gram in _grams(text):
            bucket = _bucket(gram, self.dimension)
            counts[bucket] = counts.get(bucket, 0) + 1
        values = [0.0] * self.dimension
        n_docs = max(1, self._docs)
        for bucket, count in counts.items():
            df = self._df.get(bucket, 0)
            idf = math.log((1.0 + n_docs) / (1.0 + df)) + 1.0
            values[bucket] = (1.0 + math.log(count)) * idf
        return _l2_normalize(values)


class SentenceTransformerEmbedder:
    """Optional local model. Requires the ``embeddings`` extra.

    Importing this class does not import sentence-transformers. Constructing
    it does, and the model weights download the first time that package loads
    them. This adapter never calls a paid API.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Install the embeddings extra to use SentenceTransformerEmbedder: "
                "pip install 'midplane[embeddings]'"
            ) from exc
        self._model = SentenceTransformer(model_name)
        probe = self._model.encode(["dimension probe"], normalize_embeddings=True)
        self.dimension = int(len(probe[0]))
        self.model_name = model_name

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, vector)) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OpenAICompatibleEmbedder:
    """Optional HTTP embeddings client. Tests must not construct a live call.

    The request goes to ``{base_url}/embeddings``. Dimension is whatever the
    provider returns. The pgvector column in this repo is ``vector(256)`` for
    the hashed embedder, so pointing this adapter at Postgres needs a new
    migration. No call is made from ``__init__``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("An API key is required for OpenAICompatibleEmbedder")
        if not model:
            raise RuntimeError("A model id is required for OpenAICompatibleEmbedder")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.dimension = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = _post_json(
            f"{self._base_url}/embeddings",
            {"model": self._model, "input": list(texts)},
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            self._timeout,
        )
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError("embeddings response did not return one vector per input")
        ordered = sorted(rows, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise RuntimeError("embeddings response is missing a vector")
            vectors.append(_l2_normalize([float(value) for value in embedding]))
        self.dimension = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise RuntimeError("embeddings provider timed out") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("embeddings provider returned non-JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("embeddings provider returned a non-object body")
    return parsed
