"""Export episodes as JSONL for training.

One JSON object per line:

* episode_id, source, created_at, score, score_version, outcome
* model_choice: the router record when the episode has one
* prompt: the user text reconstructed for a code_review input
* messages: [{role: user, content: prompt}, {role: assistant, content: outputs JSON}]

RECONCILE with dev_guild_prompt_templates.md if the training prompt must be the
guild template rather than this reconstruction.
RECONCILE with episode_schema.sql for score and created_at.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from midplane.prompts import render_code_review_prompt
from midplane.store import Episode, open_store

SOURCES = ("dev_guild", "trading", "app")


def training_record(episode: Episode) -> dict:
    inputs = episode.inputs or {}
    prompt = render_code_review_prompt(
        repo=str(inputs.get("repo", "")),
        instructions=str(inputs.get("instructions", "")),
        diff=str(inputs.get("diff", "")),
    )
    assistant = json.dumps(episode.outputs, sort_keys=True, separators=(",", ":"))
    model_choice = next(
        (
            item
            for item in episode.model_choices
            if isinstance(item, dict) and item.get("kind") == "route"
        ),
        None,
    )
    return {
        "episode_id": episode.run_id,
        "source": episode.source,
        "created_at": episode.created_at,
        "score": episode.quality_score,
        "score_version": episode.score_version,
        "outcome": episode.outcome,
        "model_choice": model_choice,
        "prompt": prompt,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant},
        ],
    }


def export_episodes(
    *,
    destination: Path,
    since: str | None = None,
    min_score: float | None = None,
    source: str | None = None,
    database_url: str | None = None,
    page_size: int = 200,
) -> int:
    store = open_store(database_url)
    written = 0
    cursor = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            while True:
                page, cursor = store.list_episodes(
                    source=source,
                    min_score=min_score,
                    since=since,
                    cursor=cursor,
                    limit=page_size,
                )
                for episode in page:
                    handle.write(json.dumps(training_record(episode), separators=(",", ":"), sort_keys=True))
                    handle.write("\n")
                    written += 1
                if cursor is None:
                    break
    finally:
        store.close()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m midplane.export")
    parser.add_argument("--since", default=None, help="ISO-8601 UTC lower bound on created_at, inclusive")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--source", choices=SOURCES, default=None)
    parser.add_argument("--out", required=True, help="JSONL output path")
    parser.add_argument("--database", default=None, help="Defaults to DATABASE_URL, then MIDPLANE_DATABASE_URL")
    args = parser.parse_args(argv)
    count = export_episodes(
        destination=Path(args.out),
        since=args.since,
        min_score=args.min_score,
        source=args.source,
        database_url=args.database,
    )
    print(count, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
