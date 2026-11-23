"""Export episodes as disjoint train and eval JSONL sets.

CLI: python -m midplane.export --out-dir path [--eval-fraction 0.2] [--seed text]

The split is a pure function of (seed, run_id), so an episode stays in the same
set across exports and re-runs, and the two sets never share a run_id. The
quality-score rubric is not defined yet; `--min-quality` filters on whatever
score the episode carries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from midplane.store import Episode, EpisodeStore, open_store

_BUCKETS = 10_000


def is_eval(run_id: str, *, eval_fraction: float, seed: str = "") -> bool:
    digest = hashlib.sha256(f"{seed}:{run_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % _BUCKETS
    return bucket < round(eval_fraction * _BUCKETS)


def split_episodes(
    episodes: list[Episode],
    *,
    eval_fraction: float = 0.2,
    seed: str = "",
    min_quality: float | None = None,
    outcomes: set[str] | None = None,
) -> tuple[list[Episode], list[Episode]]:
    if not 0.0 <= eval_fraction <= 1.0:
        raise ValueError("eval_fraction must be between 0 and 1")
    train: list[Episode] = []
    evaluation: list[Episode] = []
    for episode in episodes:
        if min_quality is not None and episode.quality_score < min_quality:
            continue
        if outcomes is not None and episode.outcome not in outcomes:
            continue
        target = evaluation if is_eval(episode.run_id, eval_fraction=eval_fraction, seed=seed) else train
        target.append(episode)
    return train, evaluation


def _write_jsonl(path: Path, episodes: list[Episode]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for episode in episodes:
            handle.write(json.dumps(asdict(episode), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def export_episodes(
    store: EpisodeStore,
    out_dir: Path,
    *,
    eval_fraction: float = 0.2,
    seed: str = "",
    min_quality: float | None = None,
    outcomes: set[str] | None = None,
) -> tuple[int, int]:
    train, evaluation = split_episodes(
        store.list_all(),
        eval_fraction=eval_fraction,
        seed=seed,
        min_quality=min_quality,
        outcomes=outcomes,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "train.jsonl", train)
    _write_jsonl(out_dir / "eval.jsonl", evaluation)
    return len(train), len(evaluation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m midplane.export")
    parser.add_argument("--out-dir", required=True, help="Directory for train.jsonl and eval.jsonl")
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    parser.add_argument("--seed", default="", help="Changing the seed reshuffles the split")
    parser.add_argument("--min-quality", type=float, default=None)
    parser.add_argument("--outcome", action="append", default=None, help="Keep only this outcome (repeatable)")
    parser.add_argument("--database", default=None, help="Defaults to MIDPLANE_DATABASE_URL")
    args = parser.parse_args(argv)
    try:
        train_count, eval_count = export_episodes(
            open_store(args.database),
            Path(args.out_dir),
            eval_fraction=args.eval_fraction,
            seed=args.seed,
            min_quality=args.min_quality,
            outcomes=set(args.outcome) if args.outcome else None,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"train={train_count} eval={eval_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
