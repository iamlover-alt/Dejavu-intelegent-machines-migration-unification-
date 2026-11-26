"""Start the gateway. Seeds a dev key only when MIDPLANE_SEED_DEV_KEY=1."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    if os.environ.get("MIDPLANE_SEED_DEV_KEY") == "1":
        from midplane.gateway.seed import seed

        seed()
    host = os.environ.get("MIDPLANE_HOST", "0.0.0.0")
    port = int(os.environ.get("MIDPLANE_PORT", "8000"))
    uvicorn.run("midplane.gateway.asgi:create_asgi", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
