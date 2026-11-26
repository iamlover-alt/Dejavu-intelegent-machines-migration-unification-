"""Token helpers shared by the hashed embedder and the local reranker."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9_./:-]+")
_SPLIT = re.compile(r"[^a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase tokens, plus the pieces of paths and hyphenated names.

    ``/v1/episodes`` stays one token and also yields ``v1`` and ``episodes``.
    ``trading-control`` yields itself, ``trading``, and ``control``.
    """

    found: list[str] = []
    for piece in _TOKEN.findall(text.lower()):
        found.append(piece)
        for part in _SPLIT.split(piece):
            if part and part != piece:
                found.append(part)
    return found


def token_set(text: str) -> set[str]:
    return set(tokens(text))
