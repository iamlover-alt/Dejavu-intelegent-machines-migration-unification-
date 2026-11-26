"""Mint and revoke bearer API keys.

Tokens are shown once. The database stores HMAC-SHA256(pepper, token).
The default pepper is for local development only. Set MIDPLANE_KEY_PEPPER in
any shared environment.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import sys
import uuid

from midplane.gateway.store import GatewayStore, KeyRecord

DEV_PEPPER = "dev-pepper-not-for-production"


def pepper_from_env() -> str:
    return os.environ.get("MIDPLANE_KEY_PEPPER", DEV_PEPPER)


def database_url_from_env() -> str:
    return os.environ.get("MIDPLANE_DATABASE_URL", "sqlite:///midplane_episodes.sqlite")


def hash_token(token: str, pepper_value: str | None = None) -> str:
    secret = (pepper_from_env() if pepper_value is None else pepper_value).encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return "mp_" + secrets.token_urlsafe(32)


def mint_key(store: GatewayStore, client_id: str, pepper_value: str | None = None) -> tuple[str, KeyRecord]:
    if not client_id.strip():
        raise ValueError("client id is required")
    token = new_token()
    record = store.insert_key(
        key_id="key_" + uuid.uuid4().hex[:16],
        key_hash=hash_token(token, pepper_value),
        key_prefix=token[:12],
        client_id=client_id.strip(),
    )
    return token, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m midplane.gateway.keys")
    parser.add_argument("--database", default=None, help="sqlite:///... or postgres URL")
    sub = parser.add_subparsers(dest="command", required=True)
    mint = sub.add_parser("mint", help="Create a key and print the token once")
    mint.add_argument("--client", required=True, help="Client scope, for example ios-dev")
    revoke = sub.add_parser("revoke", help="Revoke a key by id")
    revoke.add_argument("--key-id", required=True)
    args = parser.parse_args(argv)
    store = GatewayStore(args.database or database_url_from_env())
    try:
        if args.command == "mint":
            token, record = mint_key(store, args.client)
            print(token)
            print(f"key_id={record.key_id}")
            print(f"client={record.client_id}")
            return 0
        if not store.revoke_key(args.key_id):
            print(f"key not found: {args.key_id}", file=sys.stderr)
            return 2
        print(f"revoked {args.key_id}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
