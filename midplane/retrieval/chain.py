"""Multi-hop retrieval. Each step reads one store. The last list is fused.

A step builds its query from a function, from the previous hit, or from one
completion on the injected model provider. Tests pass the fake provider.
The gateway does not set a provider, so ``POST /v1/retrieve`` cannot call a model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol, Sequence

from midplane.models import ModelProvider
from midplane.retrieval.types import RetrievalResult
from midplane.retrieval.weights import FUSION_WEIGHTS, RRF_K

QueryFrom = Callable[[str, list[RetrievalResult]], str]
FiltersFrom = Callable[[str, list[RetrievalResult]], dict]


class RetrievalStore(Protocol):
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Return ranked hits for one store."""


@dataclass(frozen=True)
class ChainStep:
    store: str
    query: str | None = None
    filters: dict | None = None
    top_k: int = 5
    query_from: QueryFrom | None = None
    filters_from: FiltersFrom | None = None
    rewrite_with_model: bool = False
    use_previous: bool = False


@dataclass(frozen=True)
class HopTrace:
    hop: int
    store: str
    query: str
    filters: dict
    doc_ids: list[str]
    dropped_duplicates: list[str]
    model_rewritten: bool


@dataclass(frozen=True)
class ChainResult:
    hits: list[RetrievalResult]
    hops: list[HopTrace]
    truncated: bool
    fused: bool


# Used when the graph flag is on and the caller did not pass steps.
# No model rewrite: enabling the flag does not add a provider call.
DEFAULT_RETRIEVAL_STEPS: tuple[ChainStep, ...] = (
    ChainStep(store="episodes", top_k=3),
    ChainStep(store="schema", top_k=2, use_previous=True),
)


class RetrievalChain:
    def __init__(
        self,
        stores: Mapping[str, RetrievalStore],
        *,
        max_hops: int = 3,
        provider: ModelProvider | None = None,
        model_id: str = "fake-small",
    ) -> None:
        if max_hops < 1:
            raise ValueError("max_hops must be at least 1")
        self.stores = dict(stores)
        self.max_hops = max_hops
        self.provider = provider
        self.model_id = model_id

    def run(self, query: str, steps: Sequence[ChainStep]) -> ChainResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        hops: list[HopTrace] = []
        hop_hits: list[list[RetrievalResult]] = []
        seen: set[str] = set()
        previous: list[RetrievalResult] = []
        truncated = False
        for index, step in enumerate(steps):
            if index >= self.max_hops:
                truncated = True
                break
            store = self.stores.get(step.store)
            if store is None:
                known = ", ".join(sorted(self.stores)) or "(none)"
                raise ValueError(f"Unknown retrieval store: {step.store}. Known stores: {known}")
            step_query, rewritten = _query_for_step(
                step,
                query,
                previous,
                provider=self.provider,
                model_id=self.model_id,
            )
            filters = step.filters_from(query, previous) if step.filters_from is not None else dict(step.filters or {})
            hits = store.retrieve(step_query, top_k=step.top_k, filters=filters)
            dropped = [hit.doc_id for hit in hits if hit.doc_id in seen]
            for hit in hits:
                seen.add(hit.doc_id)
            hops.append(
                HopTrace(
                    hop=index,
                    store=step.store,
                    query=step_query,
                    filters=filters,
                    doc_ids=[hit.doc_id for hit in hits],
                    dropped_duplicates=dropped,
                    model_rewritten=rewritten,
                )
            )
            hop_hits.append(hits)
            previous = hits
        if len(hop_hits) > 1:
            return ChainResult(hits=_fuse(hop_hits, steps[: len(hop_hits)]), hops=hops, truncated=truncated, fused=True)
        only = hop_hits[0] if hop_hits else []
        return ChainResult(hits=list(only), hops=hops, truncated=truncated, fused=False)


def _query_for_step(
    step: ChainStep,
    original: str,
    previous: list[RetrievalResult],
    *,
    provider: ModelProvider | None,
    model_id: str,
) -> tuple[str, bool]:
    if step.rewrite_with_model:
        if provider is None:
            raise RuntimeError("rewrite_with_model requires a provider. Tests pass the fake provider.")
        prompt = _rewrite_prompt(original, previous)
        text = provider.complete(model_id=model_id, prompt=prompt).text.strip()
        if not text:
            raise RuntimeError("the model returned an empty retrieval query")
        return text, True
    if step.query_from is not None:
        return step.query_from(original, previous), False
    base = step.query or original
    if step.use_previous and previous:
        label = str(previous[0].metadata.get("name") or previous[0].doc_id)
        base = f"{base} {label}".strip()
    return base, False


def _rewrite_prompt(original: str, previous: list[RetrievalResult]) -> str:
    lines = [f"- {hit.doc_id}: {hit.text[:300]}" for hit in previous[:3]]
    joined = "\n".join(lines) if lines else "(none)"
    return (
        "You rewrite a retrieval query. Reply with the query text only. Do not reply with JSON.\n"
        f"Question: {original}\n"
        f"Previous hits:\n{joined}\n"
    )


def _fuse(hop_hits: Sequence[Sequence[RetrievalResult]], steps: Sequence[ChainStep]) -> list[RetrievalResult]:
    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}
    for hits, step in zip(hop_hits, steps):
        weight = FUSION_WEIGHTS.get(step.store, 1.0)
        for rank, hit in enumerate(hits, start=1):
            scores[hit.doc_id] = scores.get(hit.doc_id, 0.0) + weight / (RRF_K + rank)
            current = best.get(hit.doc_id)
            if current is None or hit.score > current.score:
                best[hit.doc_id] = hit
    fused: list[RetrievalResult] = []
    for doc_id, fusion in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
        fusion_score = round(fusion, 6)
        fused.append(replace(best[doc_id], score=fusion_score, fusion_score=fusion_score))
    return fused


def retrieval_payload(result: ChainResult, *, limit: int = 5) -> dict:
    """Compact state payload. The full text stays in the index."""

    return {
        "hits": [
            {
                "doc_id": hit.doc_id,
                "store": hit.store,
                "score": hit.score,
                "text": hit.text[:300],
                "warning": hit.breakdown.warning,
            }
            for hit in result.hits[:limit]
        ],
        "hops": [
            {
                "hop": hop.hop,
                "store": hop.store,
                "query": hop.query,
                "doc_ids": list(hop.doc_ids),
                "dropped_duplicates": list(hop.dropped_duplicates),
                "model_rewritten": hop.model_rewritten,
            }
            for hop in result.hops
        ],
        "truncated": result.truncated,
        "fused": result.fused,
    }


def retrieval_prefix(payload: Mapping) -> str:
    """Text prepended to the code-review prompt. Empty when there are no hits."""

    hits = payload.get("hits") or []
    if not hits:
        return ""
    lines = []
    for hit in list(hits)[:3]:
        if not isinstance(hit, dict):
            continue
        lines.append(f"- {hit.get('doc_id')}: {hit.get('text')}")
    if not lines:
        return ""
    return "Retrieved context:\n" + "\n".join(lines) + "\n"
