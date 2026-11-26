"""Documents, scores, and the filter language shared by every store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

EQUALITY_KEYS = (
    "source",
    "outcome",
    "status",
    "kind",
    "decision_point",
    "agent",
    "name",
    "table",
    "method",
    "path",
    "chunk",
)
RANGE_KEYS = ("min_score", "since", "until", "run_ids")
_INTERNAL = {"_text", "_fields"}


class FilterError(ValueError):
    """A retrieval filter is unknown or the wrong type."""


@dataclass(frozen=True)
class Document:
    doc_id: str
    store: str
    text: str
    metadata: dict
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Every number that produced ``final``. ``knn`` is the candidate score.

    The final value is ``rerank * prior``. ``knn`` explains which neighbors
    the index returned. It is not multiplied into ``final``.
    """

    knn: float
    lexical: float
    field_match: float
    prior: float
    rerank: float
    final: float
    warning: str | None
    weights: dict
    terms: dict
    reranker: str

    def as_dict(self) -> dict:
        return {
            "knn": self.knn,
            "lexical": self.lexical,
            "field_match": self.field_match,
            "prior": self.prior,
            "rerank": self.rerank,
            "final": self.final,
            "warning": self.warning,
            "weights": self.weights,
            "terms": self.terms,
            "reranker": self.reranker,
        }


@dataclass(frozen=True)
class RetrievalResult:
    doc_id: str
    store: str
    text: str
    metadata: dict
    breakdown: ScoreBreakdown
    score: float
    fusion_score: float | None = None


@dataclass(frozen=True)
class Neighbor:
    doc_id: str
    score: float
    metadata: dict


def public_metadata(metadata: Mapping) -> dict:
    return {key: value for key, value in metadata.items() if key not in _INTERNAL}


def stored_metadata(document: Document) -> dict:
    payload = dict(document.metadata)
    payload["store"] = document.store
    payload["_text"] = document.text
    payload["_fields"] = dict(document.fields)
    return payload


def document_from_hit(doc_id: str, metadata: Mapping) -> Document:
    fields = metadata.get("_fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    text = metadata.get("_text") or ""
    return Document(
        doc_id=doc_id,
        store=str(metadata.get("store") or ""),
        text=str(text),
        metadata=public_metadata(metadata),
        fields={str(key): str(value) for key, value in fields.items()},
    )


def _round(value: float) -> float:
    return round(float(value), 6)


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def validate_filters(filters: Mapping | None) -> dict:
    """Return a cleaned filter dict. Unknown keys raise FilterError."""

    if not filters:
        return {}
    unknown = sorted(set(filters) - set(EQUALITY_KEYS) - set(RANGE_KEYS))
    if unknown:
        raise FilterError(f"Unknown filter: {', '.join(unknown)}")
    cleaned: dict = {}
    for key in EQUALITY_KEYS:
        if key not in filters or filters[key] is None:
            continue
        value = filters[key]
        if not isinstance(value, str):
            raise FilterError(f"{key} must be a string")
        cleaned[key] = value
    if "min_score" in filters and filters["min_score"] is not None:
        try:
            score = float(filters["min_score"])
        except (TypeError, ValueError) as exc:
            raise FilterError("min_score must be a number") from exc
        if not 0.0 <= score <= 1.0:
            raise FilterError("min_score must be from 0 to 1")
        cleaned["min_score"] = score
    for key in ("since", "until"):
        if key in filters and filters[key] is not None:
            if not isinstance(filters[key], str) or not filters[key].strip():
                raise FilterError(f"{key} must be a timestamp string")
            cleaned[key] = filters[key]
    if "run_ids" in filters and filters["run_ids"] is not None:
        run_ids = filters["run_ids"]
        if isinstance(run_ids, str) or not isinstance(run_ids, (list, tuple, set)):
            raise FilterError("run_ids must be a list of strings")
        cleaned["run_ids"] = [str(item) for item in run_ids]
    return cleaned


def metadata_matches(metadata: Mapping, filters: Mapping | None) -> bool:
    """True when metadata satisfies the cleaned or raw filter dict."""

    active = filters if filters is not None else {}
    if not active:
        return True
    if "run_ids" in active and str(metadata.get("run_id") or "") not in set(active["run_ids"]):
        return False
    if "min_score" in active:
        raw = metadata.get("quality_score")
        if raw is None:
            return False
        try:
            score = float(raw)
        except (TypeError, ValueError):
            return False
        if score < float(active["min_score"]):
            return False
    if "since" in active and str(metadata.get("created_at") or "") < str(active["since"]):
        return False
    if "until" in active and str(metadata.get("created_at") or "") > str(active["until"]):
        return False
    for key in EQUALITY_KEYS:
        if key in active and str(metadata.get(key) or "") != str(active[key]):
            return False
    return True
