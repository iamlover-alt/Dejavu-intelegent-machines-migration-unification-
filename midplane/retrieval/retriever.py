"""Retrieve: embed, kNN, filter, rerank, then ``rerank * store prior``."""

from __future__ import annotations

from typing import Mapping, Sequence

from midplane.retrieval.embedder import Embedder
from midplane.retrieval.index import VectorIndex
from midplane.retrieval.rerank import Reranker
from midplane.retrieval.types import (
    Document,
    RetrievalResult,
    ScoreBreakdown,
    document_from_hit,
    stored_metadata,
    validate_filters,
)


class Retriever:
    def __init__(self, *, name: str, embedder: Embedder, index: VectorIndex, reranker: Reranker) -> None:
        self.name = name
        self.embedder = embedder
        self.index = index
        self.reranker = reranker

    def replace(self, documents: Sequence[Document]) -> None:
        if not documents:
            self.index.replace_all([])
            return
        self.embedder.fit([document.text for document in documents])
        vectors = self.embedder.embed([document.text for document in documents])
        rows = [
            (document.doc_id, vector, stored_metadata(document))
            for document, vector in zip(documents, vectors)
        ]
        self.index.replace_all(rows)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalResult]:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        active = validate_filters(filters)
        pool = candidate_k if candidate_k is not None else max(top_k * 5, 80)
        neighbors = self.index.search(self.embedder.embed_query(query), pool, active)
        documents = [document_from_hit(neighbor.doc_id, neighbor.metadata) for neighbor in neighbors]
        parts = self.reranker.score_documents(query, documents) if documents else []
        results: list[RetrievalResult] = []
        for neighbor, document, part in zip(neighbors, documents, parts):
            breakdown = ScoreBreakdown(
                knn=_round(neighbor.score),
                lexical=part.lexical,
                field_match=part.field_match,
                prior=part.prior,
                rerank=part.content,
                final=part.score,
                warning=part.warning,
                weights=part.weights,
                terms=part.terms,
                reranker=part.backend,
            )
            if part.score <= 0:
                continue
            results.append(
                RetrievalResult(
                    doc_id=document.doc_id,
                    store=document.store or self.name,
                    text=document.text,
                    metadata=document.metadata,
                    breakdown=breakdown,
                    score=part.score,
                )
            )
        results.sort(key=lambda item: (-item.score, item.doc_id))
        return results[:top_k]


def _round(value: float) -> float:
    return round(float(value), 6)
