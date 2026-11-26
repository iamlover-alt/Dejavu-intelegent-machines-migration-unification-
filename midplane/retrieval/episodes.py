"""Episode memory: chunks of inputs, outputs, findings, and the model choice.

Weights are quality, then recency, then source. Indexing one episode replaces
that episode's chunks, so a later upsert updates the index.
"""

from __future__ import annotations

import threading
from typing import Sequence

from midplane.retrieval.embedder import Embedder, HashedNgramEmbedder
from midplane.retrieval.index import VectorIndex, open_index
from midplane.retrieval.rerank import LexicalReranker
from midplane.retrieval.retriever import Retriever
from midplane.retrieval.types import Document, RetrievalResult
from midplane.retrieval.weights import episode_prior
from midplane.store import Episode, EpisodeStore, episode_timestamp


def chunk_episode(episode: Episode) -> list[Document]:
    """One document per input block, output summary, finding, and model choice."""

    metadata = {
        "store": "episodes",
        "run_id": episode.run_id,
        "source": episode.source,
        "quality_score": float(episode.quality_score),
        "created_at": episode.created_at,
        "outcome": episode.outcome,
        "score_version": episode.score_version,
    }
    documents: list[Document] = []
    inputs = episode.inputs or {}
    documents.append(
        _chunk(
            episode,
            "inputs",
            " ".join(
                [
                    "episode inputs",
                    str(inputs.get("role", "")),
                    str(inputs.get("repo", "")),
                    str(inputs.get("instructions", "")),
                    str(inputs.get("diff", "")),
                ]
            ),
            metadata,
        )
    )
    outputs = episode.outputs or {}
    documents.append(
        _chunk(
            episode,
            "outputs",
            f"episode output summary {outputs.get('summary', '')}",
            metadata,
        )
    )
    findings = outputs.get("findings") if isinstance(outputs, dict) else None
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            text = (
                f"finding {finding.get('severity', '')} {finding.get('detail', '')} "
                f"ref {finding.get('ref', '')}"
            )
            documents.append(_chunk(episode, f"finding-{index}", text, metadata))
    choice_bits: list[str] = []
    for item in episode.model_choices or []:
        if not isinstance(item, dict):
            continue
        choice_bits.append(
            " ".join(
                str(item.get(key) or "")
                for key in ("kind", "primary", "fallback", "model_id", "role", "task_type")
            )
        )
    documents.append(
        _chunk(episode, "model-choice", "model choice " + " ".join(choice_bits), metadata)
    )
    return documents


def _chunk(episode: Episode, chunk: str, text: str, metadata: dict) -> Document:
    payload = dict(metadata)
    payload["chunk"] = chunk
    return Document(
        doc_id=f"{episode.run_id}:{chunk}",
        store="episodes",
        text=" ".join(text.split()),
        metadata=payload,
        fields={"source": episode.source, "chunk": chunk, "run_id": episode.run_id},
    )


class EpisodeMemoryStore:
    """In-process view of EpisodeStore for retrieval.

    ``reindex`` reads the episode table. ``index_episode`` is the incremental
    path the graph calls after a write. Both rebuild vectors for the chunks
    this process is holding.
    """

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        index: VectorIndex | None = None,
        database_url: str | None = None,
        now: str | None = None,
    ) -> None:
        self.now = now or episode_timestamp()
        self.embedder = embedder or HashedNgramEmbedder()
        self.index = index or open_index(
            database_url,
            store_name="episodes",
            dimension=self.embedder.dimension,
        )
        self._lock = threading.RLock()
        self._docs: dict[str, Document] = {}
        self.retriever = Retriever(
            name="episodes",
            embedder=self.embedder,
            index=self.index,
            reranker=LexicalReranker(lambda document: episode_prior(document, now=self.now)),
        )

    def close(self) -> None:
        self.index.close()

    def guard(self):
        """Serialize reindex and search. Reentrant, so retrieve can nest inside it."""

        return self._lock

    def index_episode(self, episode: Episode) -> None:
        """Replace chunks for this run id and re-embed the process corpus."""

        with self._lock:
            self._docs = {
                doc_id: document
                for doc_id, document in self._docs.items()
                if document.metadata.get("run_id") != episode.run_id
            }
            for document in chunk_episode(episode):
                self._docs[document.doc_id] = document
            self.retriever.replace(list(self._docs.values()))

    def reindex(self, store: EpisodeStore, *, run_ids: Sequence[str] | None = None) -> int:
        """Replace the corpus with chunks from ``store``. Returns the chunk count."""

        documents: list[Document] = []
        for episode in _iter_episodes(store, run_ids=run_ids):
            documents.extend(chunk_episode(episode))
        with self._lock:
            self._docs = {document.doc_id: document for document in documents}
            self.retriever.replace(documents)
        return len(documents)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalResult]:
        with self._lock:
            return self.retriever.retrieve(query, top_k=top_k, filters=filters, candidate_k=candidate_k)


def _iter_episodes(store: EpisodeStore, *, run_ids: Sequence[str] | None) -> list[Episode]:
    episodes: list[Episode] = []
    cursor: str | None = None
    while True:
        page, cursor = store.list_episodes(run_ids=run_ids, cursor=cursor, limit=100)
        episodes.extend(page)
        if cursor is None:
            return episodes
