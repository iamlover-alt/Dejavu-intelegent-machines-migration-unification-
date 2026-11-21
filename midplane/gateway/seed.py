"""Create or reprint the local ios-dev API key.

The plaintext is written to MIDPLANE_DEV_KEY_FILE (default ./.dev-api-key)
so a restart can print the same key. That file is a local secret.
"""

from __future__ import annotations

import os
from pathlib import Path

from midplane.gateway.keys import database_url_from_env, hash_token, mint_key, pepper_from_env
from midplane.gateway.store import GatewayStore

DEV_CLIENT = "ios-dev"


def dev_key_path() -> Path:
    return Path(os.environ.get("MIDPLANE_DEV_KEY_FILE", ".dev-api-key"))


def seed(database_url: str | None = None) -> str:
    path = dev_key_path()
    store = GatewayStore(database_url or database_url_from_env())
    pepper = pepper_from_env()
    try:
        if path.is_file():
            existing = path.read_text().strip()
            if existing and store.find_active_key(hash_token(existing, pepper)) is not None:
                _print_key(existing)
                return existing
        token, _record = mint_key(store, DEV_CLIENT, pepper)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _print_key(token)
        return token
    finally:
        store.close()


def _print_key(token: str) -> None:
    print(f"dev api key: {token}")
    print(f"client: {DEV_CLIENT}")


def main() -> int:
    seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
