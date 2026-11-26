"""Rerankers. The default is local. The cross-encoder is an optional adapter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from midplane.retrieval.text import token_set
from midplane.retrieval.types import Document, _round
from midplane.retrieval.weights import PriorResult, content_score


@dataclass(frozen=True)
class RerankParts:
    lexical: float
    field_match: float
    prior: float
    content: float
    score: float
    warning: str | None
    terms: dict
    weights: dict
    backend: str


class Reranker(Protocol):
    def score_documents(self, query: str, documents: Sequence[Document]) -> list[RerankParts]:
        """Score documents for one query. Order matches ``documents``."""


PriorFn = Callable[[Document], PriorResult]


def lexical_overlap(query: str, text: str) -> float:
    """Fraction of query tokens that appear in the document."""

    query_tokens = token_set(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & token_set(text)) / len(query_tokens)


def field_match_score(query: str, fields: dict[str, str]) -> float:
    """Fraction of query tokens that appear in named fields (path, name, title)."""

    query_tokens = token_set(query)
    if not query_tokens or not fields:
        return 0.0
    field_tokens: set[str] = set()
    for value in fields.values():
        field_tokens.update(token_set(value))
    if not field_tokens:
        return 0.0
    return len(query_tokens & field_tokens) / len(query_tokens)


def _parts(
    *,
    lexical: float,
    field_match: float,
    content: float,
    prior: PriorResult,
    backend: str,
) -> RerankParts:
    lexical_value = _round(lexical)
    field_value = _round(field_match)
    content_value = _round(content)
    return RerankParts(
        lexical=lexical_value,
        field_match=field_value,
        prior=prior.factor,
        content=content_value,
        score=_round(content_value * prior.factor),
        warning=prior.warning,
        terms=dict(prior.terms),
        weights=dict(prior.weights),
        backend=backend,
    )


class LexicalReranker:
    """Lexical overlap, field matches, and a store-supplied metadata prior.

    The content score is ``0.70 * overlap + 0.30 * field_match``. The store
    prior multiplies that content score. The product is ``score``.
    """

    backend = "lexical"

    def __init__(self, prior_fn: PriorFn) -> None:
        self._prior_fn = prior_fn

    def score_documents(self, query: str, documents: Sequence[Document]) -> list[RerankParts]:
        scored: list[RerankParts] = []
        for document in documents:
            lexical = lexical_overlap(query, document.text)
            fields = field_match_score(query, document.fields)
            content = content_score(lexical, fields)
            scored.append(
                _parts(
                    lexical=lexical,
                    field_match=fields,
                    content=content,
                    prior=self._prior_fn(document),
                    backend=self.backend,
                )
            )
        return scored


class CrossEncoderReranker:
    """Optional sentence-transformers cross-encoder.

    The encoder score is mixed with the local lexical score, then the store
    prior still multiplies the result. Constructing this class loads the
    model. The default process never does that.
    """

    backend = "cross-encoder"

    def __init__(
        self,
        prior_fn: PriorFn,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "Install the embeddings extra to use CrossEncoderReranker: "
                "pip install 'midplane[embeddings]'"
            ) from exc
        self._prior_fn = prior_fn
        self._model = CrossEncoder(model_name)
        self.model_name = model_name

    def score_documents(self, query: str, documents: Sequence[Document]) -> list[RerankParts]:
        if not documents:
            return []
        raw_scores = self._model.predict([(query, document.text) for document in documents])
        scored: list[RerankParts] = []
        for document, raw in zip(documents, raw_scores):
            lexical = lexical_overlap(query, document.text)
            fields = field_match_score(query, document.fields)
            local = content_score(lexical, fields)
            cross = 1.0 / (1.0 + math.exp(-float(raw)))
            content = 0.5 * local + 0.5 * cross
            scored.append(
                _parts(
                    lexical=lexical,
                    field_match=fields,
                    content=content,
                    prior=self._prior_fn(document),
                    backend=self.backend,
                )
            )
        return scored
