"""ASGI factory used by uvicorn. The database comes from the environment."""

from __future__ import annotations

from midplane.gateway.app import create_app


def create_asgi():
    return create_app()
