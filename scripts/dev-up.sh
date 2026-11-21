#!/bin/sh
# One-command local gateway: Postgres, the API, and a printed dev API key.
set -eu
cd "$(dirname "$0")/.."
exec docker compose up --build
