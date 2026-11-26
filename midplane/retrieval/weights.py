"""Store weights. The final score is ``rerank * prior``.

``rerank`` is lexical overlap and field match. ``prior`` is the store weight:
quality, then recency, then source for episodes; outcome for traces; wired
versus planned for schema documents. ``knn`` only chooses the candidate pool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from midplane.retrieval.types import Document, _round, clamp01

LEXICAL_WEIGHT = 0.70
FIELD_WEIGHT = 0.30

QUALITY_FLOOR = 0.25
QUALITY_SPAN = 0.75
RECENCY_FLOOR = 0.70
RECENCY_SPAN = 0.30
SOURCE_FLOOR = 0.70
SOURCE_SPAN = 0.30
RECENCY_HALF_LIFE_DAYS = 14.0

SOURCE_PRIOR = {
    "dev_guild": 1.0,
    "app": 0.65,
    "trading": 0.25,
}

TRACE_FLOOR = 0.20
TRACE_SPAN = 0.80
OUTCOME_PRIOR = {
    "success": 1.0,
    "unknown": 0.55,
    "fail": 0.15,
}

SCHEMA_FLOOR = 0.60
SCHEMA_SPAN = 0.40
STATUS_PRIOR = {
    "wired": 1.0,
    "planned": 0.55,
}

FUSION_WEIGHTS = {
    "episodes": 1.0,
    "schema": 1.0,
    "traces": 0.85,
}
RRF_K = 60

WARNING_FAILURE = "failure"
WARNING_PLANNED = "planned"

CONTENT_WEIGHTS = {
    "lexical": LEXICAL_WEIGHT,
    "field_match": FIELD_WEIGHT,
}


@dataclass(frozen=True)
class PriorResult:
    factor: float
    terms: dict
    warning: str | None
    weights: dict


def content_score(lexical: float, field_match: float) -> float:
    return _round(LEXICAL_WEIGHT * clamp01(lexical) + FIELD_WEIGHT * clamp01(field_match))


def recency_factor(created_at: str, now: str) -> float:
    """1.0 at ``now``, about 0.5 after one half-life. Missing stamps return 0.5."""

    if not created_at or not now:
        return 0.5
    try:
        then = _parse_time(created_at)
        current = _parse_time(now)
    except ValueError:
        return 0.5
    age_days = max(0.0, (current - then).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rounded_terms(terms: dict[str, float]) -> dict[str, float]:
    return {key: _round(value) for key, value in terms.items()}


def episode_prior(document: Document, *, now: str) -> PriorResult:
    """Quality dominates, then recency, then source."""

    metadata = document.metadata
    quality = clamp01(float(metadata.get("quality_score") or 0.0))
    recency = recency_factor(str(metadata.get("created_at") or ""), now)
    source_prior = SOURCE_PRIOR.get(str(metadata.get("source") or ""), 0.5)
    quality_term = QUALITY_FLOOR + QUALITY_SPAN * quality
    recency_term = RECENCY_FLOOR + RECENCY_SPAN * recency
    source_term = SOURCE_FLOOR + SOURCE_SPAN * source_prior
    factor = quality_term * recency_term * source_term
    warning = None
    if str(metadata.get("outcome") or "") == "error":
        warning = WARNING_FAILURE
    return PriorResult(
        factor=_round(factor),
        terms=_rounded_terms(
            {
                "quality": quality,
                "quality_term": quality_term,
                "recency": recency,
                "recency_term": recency_term,
                "source_prior": source_prior,
                "source_term": source_term,
            }
        ),
        warning=warning,
        weights={
            **CONTENT_WEIGHTS,
            "quality_floor": QUALITY_FLOOR,
            "quality_span": QUALITY_SPAN,
            "recency_floor": RECENCY_FLOOR,
            "recency_span": RECENCY_SPAN,
            "source_floor": SOURCE_FLOOR,
            "source_span": SOURCE_SPAN,
            "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        },
    )


def trace_prior(document: Document, *, now: str | None = None) -> PriorResult:
    """Successes are boosted. Failures stay in the list and carry a warning."""

    del now
    outcome = str(document.metadata.get("outcome") or "unknown")
    if outcome not in OUTCOME_PRIOR:
        outcome = "unknown"
    prior = OUTCOME_PRIOR[outcome]
    factor = TRACE_FLOOR + TRACE_SPAN * prior
    warning = WARNING_FAILURE if outcome == "fail" else None
    return PriorResult(
        factor=_round(factor),
        terms=_rounded_terms({"outcome_prior": prior, "outcome_term": factor}),
        warning=warning,
        weights={
            **CONTENT_WEIGHTS,
            "trace_floor": TRACE_FLOOR,
            "trace_span": TRACE_SPAN,
            **{f"outcome_{name}": value for name, value in OUTCOME_PRIOR.items()},
        },
    )


def schema_prior(document: Document, *, now: str | None = None) -> PriorResult:
    """Wired documents outrank planned ones. Planned rows stay searchable."""

    del now
    status = str(document.metadata.get("status") or "")
    prior = STATUS_PRIOR.get(status, 0.55)
    factor = SCHEMA_FLOOR + SCHEMA_SPAN * prior
    warning = WARNING_PLANNED if status == "planned" else None
    return PriorResult(
        factor=_round(factor),
        terms=_rounded_terms({"status_prior": prior, "status_term": factor}),
        warning=warning,
        weights={
            **CONTENT_WEIGHTS,
            "schema_floor": SCHEMA_FLOOR,
            "schema_span": SCHEMA_SPAN,
            **{f"status_{name}": value for name, value in STATUS_PRIOR.items()},
        },
    )
