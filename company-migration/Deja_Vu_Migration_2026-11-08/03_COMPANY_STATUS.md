# Verified project status and migration handoff — 2026-11-08

## Company identity and architecture
- **Company**: Déjà Vu Intelligence Machines™. Motto from company planning: **“We aim to focus on what matters.”**
- **Platform vision:** secure, model-agnostic intelligence control plane across models, tools, agents, evidence, memory, permissions, workflows, devices, sensors and potential robotics integrations. Healthcare-grade security is a target, not an achieved certification.
- **Product Zero:** user goal → reasoning → evidence → hypothesis scores → policy-gated action → evaluation → memory/next action. Require explicit paper/live and observation/action boundaries.
- **Voice Dev / Presence / Capability Learning / cross-layer Pattern-to-Creation**: research and design strands preserved in founder documents, not independently verified deployed products.

## Product portfolio
- **Multi-agent platform:** skill registry, orchestration, weighted memory, provenance, documentation agent **minime**, and Developer Guild automation. Preserved packages include AI Hub Phase 1, Claude-connected and Unison versions, Developer Guild V2, 50-bot adaptive cluster, and minime v1. Presence of a package is not proof of production readiness.
- **Trading-bot repository:** `https://github.com/yyv5zgkpsr-gif/Trading-bots-`. Default branch `claude/multi-agent-trading-orchestrator-9ciu2t`; October 26 snapshot commit `c23ee716ea77b71a91f8e78d750c3ad6cf268b01` (79 files), verified current commit `a308fbfb557e9c8dfc4cae2ea1b7791068d7c543` (111 files, +5 commits).
- **Whimsical:** community marketplace brand idea (“The Whimsical People’s Club of America”; “Buy. Sell. Trade. Vibe. Share. Care.”), local coffee reward idea, brand assets saved. GitHub `yyv5zgkpsr-gif/Whimsical-` returned 404 at export time; original app repository source is **not** in the verified source snapshot.
- **Other concepts:** voice-input Moment Log/event tracker, intelligence/voice-first product experiences, founder/IP chronology, recipient-aware predictive assistance RPA-01. The October 8 folder retained RPA-01 as a parked future branch rather than current Product Zero scope.

## Verified external-system status
- **GitHub PR #1** (`https://github.com/yyv5zgkpsr-gif/Trading-bots-/pull/1`): merged 2026-10-14, env propagation and paper-MCP integration tests. PR body reports 34 passing tests (not rerun during export).
- **GitHub PR #2** (`https://github.com/yyv5zgkpsr-gif/Trading-bots-/pull/2`): **open, unmerged** on 2026-11-08; exit monitor, risk ledger, live crypto handling, dashboard and deployment material documented in PR body. PR body reports 52 passing tests (not rerun during export). Warnings: polling exits, no protection when monitor is down, full-position market exits, and unverified live crypto broker path.
- **Render**: Confirmed workspace `My Workspace`. List-services returned no services; no Key Value or Postgres instances found in the workspace. This does not establish that every external/cloud environment is empty.
- **Company review cadence** from historical planning: Monday AI technology brief; Friday Product Zero/company roadmap status. These are ongoing reviews, not files containing every underlying chat.

## Migration decision gates
1. **Paper deployment:** conditional on fresh checks, tests, safe configuration, a single-process policy, log monitoring, and review of container/service auto-starts. No deployment performed by this export.
2. **Live trading:** **NO-GO** pending review and mitigation of process duplication, monitor downtime, stale or duplicate exits, ledger consistency, credential security, and broker execution failure recovery; crypto live path needs controlled broker validation.
3. **Product Zero:** keep evidence/provenance, policy/security and reversible actions ahead of automated production autonomy. No claim of HIPAA compliance.
4. **Company incorporation/IP:** prior filing and trademark plans are retained as draft/history until actual filings and counsel review are provided.

## What changed from the October 26 archive
- Added source files: `AGENTS.md`, two trading-bot automation starter ZIPs, and a non-zipped Markdown automation starter.
- Verified GitHub changes: current branch +5 commits, 32 more files than previous snapshot, PR #2 status open.
- Verified connected Render state and added migration scripts + structured status/knowledge records.
- Appended selected November company activity notes; no conversation text is represented as a full transcript.
