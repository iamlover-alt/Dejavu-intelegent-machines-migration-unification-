"""CLI: python -m midplane.run --diff path

Prints the episode run_id. The default model provider is the fake provider.
The default episode database is sqlite:///midplane_episodes.sqlite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from midplane.graph import run_review
from midplane.store import open_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m midplane.run")
    parser.add_argument("--diff", required=True, help="Path to a unified diff")
    parser.add_argument("--repo", default="local", help="Repository name stored on the task")
    parser.add_argument("--instructions", default="Review this diff.", help="Review instructions")
    parser.add_argument(
        "--database",
        default=None,
        help="Episode database URL (sqlite:///... or postgres://...). Defaults to MIDPLANE_DATABASE_URL.",
    )
    args = parser.parse_args(argv)
    diff_path = Path(args.diff)
    if not diff_path.is_file():
        print(f"diff file not found: {diff_path}", file=sys.stderr)
        return 2
    result = run_review(
        diff_path.read_text(),
        repo=args.repo,
        instructions=args.instructions,
        store=open_store(args.database),
    )
    print(result.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
