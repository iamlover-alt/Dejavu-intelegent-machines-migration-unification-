# Déjà Vu Intelligence Machines™ — Company Migration Export
**Refresh date:** 2026-11-08 (America/Los_Angeles)  
**Scope:** Déjà Vu umbrella, Product Zero, agent systems, trading-bot development, Whimsical concept, founder records and creative/brand work relevant to the business.

## How to use this export
1. Open `00_START_HERE.md` (this file), `01_MIGRATION_MANIFEST.json`, and `04_GAPS_AND_ACTIONS.md`.
2. Browse `baseline_2026-10-26/` for the full preserved October 26 company export (source documents, generated code packages, repository snapshot, and its searchable original inventory).
3. Review `new_files_since_2026-10-26/` for **four separately materialized source files**: `AGENTS.md` and three trading-bot automation starters. These were found in the accessible saved project library.
4. Open `02_COMPANY_KNOWLEDGE.jsonl` or `03_COMPANY_STATUS.md` to import company concepts, scope, decisions, hypotheses and open issues. These records are **summaries**, not original chat messages.
5. To retrieve the **current** Trading-bots source, execute `scripts/fetch_pinned_github_source.sh` in an internet-connected environment with Git installed. It clones and checks out a pinned commit, but deliberately does not execute scripts, deploy or trade. Review the code before enabling any runtime.
6. Run `python3 scripts/verify_export.py` from this folder to verify content hashes.

## Completeness and provenance
This is a merger of the accessible October 26 archive with newly discovered company files, a verified GitHub/Render status check, and reconstructed knowledge records. It is **not a full export of every ChatGPT conversation, audio recording, Git history, external secret, hosted database or deployment**. Original company files are preserved byte-for-byte inside the `baseline_2026-10-26/` directory; named historical file versions remain distinct. Inherited historical source hashes are provided in the baseline's `SHA256SUMS.txt`.

The October 26 baseline held **131 saved original files**, **79 verified GitHub snapshot files**, and partial chat excerpts and searchable text; its package contained 287 ZIP entries. The GitHub default branch currently points to `a308fbfb557e9c8dfc4cae2ea1b7791068d7c543`; its recursive tree contains **111 blobs**. That current source is **not embedded as file bytes in this export** because the source download was unavailable here. The included 79-file snapshot is pinned to `c23ee716ea77b71a91f8e78d750c3ad6cf268b01` and is 5 commits behind current. The retrieval script restores current code separately.

**As of November 8, 2026:** PR #1 is merged; PR #2 is open and mergeable, not merged. The connected Render workspace `My Workspace` returned no services in the query and no Postgres or Key Value instances. Nothing in this export authorizes live trading or starts a trading daemon.

## Recommended destination structure
```
/company-migration/
  Deja_Vu_Migration_2026-11-08/
    baseline_2026-10-26/
    new_files_since_2026-10-26/
    01_MIGRATION_MANIFEST.json
    02_COMPANY_KNOWLEDGE.jsonl
    03_COMPANY_STATUS.md
    04_GAPS_AND_ACTIONS.md
    scripts/
  current_repo_source/           # generated only after running fetch script
  account_history_original/      # add ChatGPT account export separately
```

Treat founder identity and unrelated personal context embedded in some source records as confidential; restrict access to private collaborators. Do not upload credentials, `.env` files or authentication tokens into a migration archive or public repository.
