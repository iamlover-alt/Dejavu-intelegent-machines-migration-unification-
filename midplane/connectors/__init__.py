"""Connector catalog and safety-filtered selection.

Write and side-effect capabilities stay out of a selection unless the
constraints allow them. Real-money trading is never selected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import yaml

from midplane.schema_registry.build import load_schema_registry

_EFFECTS = {"read", "write", "side_effect"}
_STATUSES = {"wired", "planned"}
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "how",
    "does",
    "what",
    "which",
    "this",
    "that",
    "into",
    "from",
    "are",
    "can",
    "use",
    "its",
    "not",
}
_LIVE_PHRASES = (
    "real money",
    "real-money",
    "real_money",
    "live trade",
    "live trading",
    "live order",
)


@dataclass(frozen=True)
class Capability:
    id: str
    effect: str
    description: str

    def to_dict(self) -> dict:
        return {"id": self.id, "effect": self.effect, "description": self.description}


@dataclass(frozen=True)
class Connector:
    name: str
    kind: str
    status: str
    capabilities: tuple[Capability, ...]
    auth_required: bool
    auth_schemes: tuple[str, ...]
    auth_notes: str
    side_effects: tuple[str, ...]
    schemas_produces: tuple[str, ...]
    schemas_consumes: tuple[str, ...]
    safety_tier: str
    real_money: bool
    examples: tuple[str, ...]
    notes: str = ""
    live_use: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "auth": {
                "required": self.auth_required,
                "schemes": list(self.auth_schemes),
                "notes": self.auth_notes,
            },
            "side_effects": list(self.side_effects),
            "schemas": {
                "produces": list(self.schemas_produces),
                "consumes": list(self.schemas_consumes),
            },
            "safety_tier": self.safety_tier,
            "real_money": self.real_money,
            "examples": list(self.examples),
            "notes": self.notes,
            "live_use": self.live_use,
        }


@dataclass(frozen=True)
class ConnectorConstraints:
    allow_side_effects: bool = False
    allow_writes: bool = False
    real_money: bool = False

    def to_dict(self) -> dict:
        return {
            "allow_side_effects": self.allow_side_effects,
            "allow_writes": self.allow_writes,
            "real_money": self.real_money,
        }

    @classmethod
    def from_mapping(cls, data: Mapping | None) -> ConnectorConstraints:
        raw = dict(data or {})

        def flag(name: str) -> bool:
            value = raw.get(name, False)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        return cls(
            allow_side_effects=flag("allow_side_effects"),
            allow_writes=flag("allow_writes"),
            real_money=flag("real_money"),
        )


@dataclass(frozen=True)
class RankedConnector:
    name: str
    kind: str
    status: str
    safety_tier: str
    real_money: bool
    score: int
    reasons: tuple[str, ...]
    allowed_capabilities: tuple[Capability, ...]
    excluded_capabilities: tuple[Capability, ...]
    schemas_produces: tuple[str, ...]
    schemas_consumes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "safety_tier": self.safety_tier,
            "real_money": self.real_money,
            "score": self.score,
            "reasons": list(self.reasons),
            "allowed_capabilities": [item.to_dict() for item in self.allowed_capabilities],
            "excluded_capabilities": [item.to_dict() for item in self.excluded_capabilities],
            "schemas": {
                "produces": list(self.schemas_produces),
                "consumes": list(self.schemas_consumes),
            },
        }


@dataclass(frozen=True)
class ExcludedConnector:
    name: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"name": self.name, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ConnectorSelection:
    selected: tuple[RankedConnector, ...]
    excluded: tuple[ExcludedConnector, ...]

    def to_dict(self) -> dict:
        return {
            "selected": [item.to_dict() for item in self.selected],
            "excluded": [item.to_dict() for item in self.excluded],
        }

    def get(self, name: str) -> RankedConnector | None:
        for item in self.selected:
            if item.name == name:
                return item
        return None


def _registry_path() -> Path:
    return Path(__file__).with_name("registry.yaml")


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("expected a boolean")


def _parse_connector(data: dict) -> Connector:
    capabilities = tuple(
        Capability(
            id=str(item["id"]),
            effect=str(item["effect"]),
            description=str(item.get("description") or ""),
        )
        for item in data["capabilities"]
    )
    for capability in capabilities:
        if capability.effect not in _EFFECTS:
            raise ValueError(f"{data['name']} capability {capability.id} has effect {capability.effect}")
    derived = tuple(item.id for item in capabilities if item.effect != "read")
    declared = tuple(str(item) for item in data.get("side_effects") or [])
    if declared != derived:
        raise ValueError(f"{data['name']} side_effects {declared} do not match non-read capabilities {derived}")
    status = str(data["status"])
    if status not in _STATUSES:
        raise ValueError(f"{data['name']} status must be wired or planned")
    auth = data.get("auth") or {}
    schemas = data.get("schemas") or {}
    return Connector(
        name=str(data["name"]),
        kind=str(data["kind"]),
        status=status,
        capabilities=capabilities,
        auth_required=_flag(auth.get("required", False)),
        auth_schemes=tuple(str(item) for item in auth.get("schemes") or []),
        auth_notes=str(auth.get("notes") or ""),
        side_effects=declared,
        schemas_produces=tuple(str(item) for item in schemas.get("produces") or []),
        schemas_consumes=tuple(str(item) for item in schemas.get("consumes") or []),
        safety_tier=str(data["safety_tier"]),
        real_money=_flag(data.get("real_money", False)),
        examples=tuple(str(item) for item in data.get("examples") or []),
        notes=str(data.get("notes") or "").strip(),
        live_use=str(data.get("live_use") or ""),
    )


def _load_raw() -> tuple[Connector, ...]:
    document = yaml.safe_load(_registry_path().read_text(encoding="utf-8"))
    connectors = tuple(_parse_connector(item) for item in document["connectors"])
    names = [item.name for item in connectors]
    if len(names) != len(set(names)):
        raise ValueError("connector registry has duplicate names")
    return connectors


@lru_cache(maxsize=1)
def load_connector_registry() -> tuple[Connector, ...]:
    connectors = _load_raw()
    known = {entry.id for entry in load_schema_registry().entries}
    missing: list[str] = []
    for connector in connectors:
        for schema_id in (*connector.schemas_produces, *connector.schemas_consumes):
            if schema_id not in known:
                missing.append(f"{connector.name}:{schema_id}")
    if missing:
        raise ValueError("connectors reference unknown schemas: " + ", ".join(missing))
    return connectors


def list_connectors() -> tuple[Connector, ...]:
    """Every connector, wired and planned."""

    return load_connector_registry()


def _tokens(goal: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", goal.lower())
        if len(token) >= 3 and token not in _STOP
    ]


def _requests_live(goal: str, constraints: ConnectorConstraints) -> bool:
    if constraints.real_money:
        return True
    text = goal.lower()
    return any(phrase in text for phrase in _LIVE_PHRASES)


def _effect_allowed(effect: str, constraints: ConnectorConstraints) -> bool:
    if effect == "read":
        return True
    if effect == "write":
        return constraints.allow_writes or constraints.allow_side_effects
    if effect == "side_effect":
        return constraints.allow_side_effects
    return False


def _is_trading(connector: Connector) -> bool:
    return connector.kind == "trading" or connector.name == "trading_control"


def select_connectors(
    goal: str,
    constraints: ConnectorConstraints | Mapping | None = None,
    connectors: tuple[Connector, ...] | None = None,
) -> ConnectorSelection:
    """Rank connectors for a goal.

    A connector is omitted when every capability is a write or side effect
    and the constraints do not allow that effect. Mixed connectors stay, limited
    to the capabilities the constraints allow. A connector with real_money set,
    and any trading connector when the goal asks for live or real-money trading,
    is excluded even if side effects are allowed.
    """

    if isinstance(constraints, ConnectorConstraints):
        resolved = constraints
    else:
        resolved = ConnectorConstraints.from_mapping(constraints)
    catalog = connectors if connectors is not None else load_connector_registry()
    live = _requests_live(goal, resolved)
    tokens = _tokens(goal)
    selected: list[RankedConnector] = []
    excluded: list[ExcludedConnector] = []
    for connector in catalog:
        if connector.real_money:
            excluded.append(
                ExcludedConnector(
                    name=connector.name,
                    reasons=("real_money is true; this agent never selects real-money trading",),
                )
            )
            continue
        if live and _is_trading(connector):
            excluded.append(
                ExcludedConnector(
                    name=connector.name,
                    reasons=(
                        "live or real-money trading was requested; it is never selected, "
                        "and hard external controls are not waived",
                    ),
                )
            )
            continue
        allowed = tuple(item for item in connector.capabilities if _effect_allowed(item.effect, resolved))
        denied = tuple(item for item in connector.capabilities if item not in allowed)
        if not allowed:
            excluded.append(
                ExcludedConnector(
                    name=connector.name,
                    reasons=("write or side-effect capabilities are not allowed",),
                )
            )
            continue
        reasons: list[str] = []
        score = 0
        if connector.status == "wired":
            score += 5
            reasons.append("wired into the platform")
        else:
            score += 1
            reasons.append("status is planned; not wired yet")
        blob = " ".join(
            [
                connector.name.replace("_", " "),
                connector.kind,
                connector.safety_tier,
                *connector.examples,
                *connector.schemas_produces,
                *connector.schemas_consumes,
            ]
        ).lower()
        hits = [token for token in tokens if token in blob]
        if hits:
            score += 3 * len(hits)
            reasons.append("matches goal: " + ", ".join(hits))
        read_ids = [item.id for item in allowed if item.effect == "read"]
        if read_ids:
            score += 1
            reasons.append("read capabilities: " + ", ".join(read_ids))
        write_ids = [item.id for item in allowed if item.effect != "read"]
        if write_ids:
            reasons.append("allowed write or side-effect capabilities: " + ", ".join(write_ids))
        if denied:
            reasons.append("excluded capabilities: " + ", ".join(item.id for item in denied))
        if _is_trading(connector):
            reasons.append(
                "paper-only; real_money is false; hard external controls are required before any live use"
            )
        reasons.append(f"safety tier {connector.safety_tier}")
        selected.append(
            RankedConnector(
                name=connector.name,
                kind=connector.kind,
                status=connector.status,
                safety_tier=connector.safety_tier,
                real_money=connector.real_money,
                score=score,
                reasons=tuple(reasons),
                allowed_capabilities=allowed,
                excluded_capabilities=denied,
                schemas_produces=connector.schemas_produces,
                schemas_consumes=connector.schemas_consumes,
            )
        )
    selected.sort(key=lambda item: (-item.score, item.name))
    excluded.sort(key=lambda item: item.name)
    return ConnectorSelection(selected=tuple(selected), excluded=tuple(excluded))


def without_real_money(connector: Connector) -> Connector:
    """Copy used by tests that need a real-money variant."""

    return replace(connector, real_money=True)
