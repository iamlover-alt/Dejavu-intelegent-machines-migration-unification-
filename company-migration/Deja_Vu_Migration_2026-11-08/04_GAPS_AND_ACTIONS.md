# Migration gaps and next actions

**Account history and voice** — ChatGPT history is not fully accessible to this export flow. The previous archive contains only partial user text, excerpts, retrieved summaries, and a founder voice-analysis document; no complete conversations or original voice recordings are included. To complete this portion, request a ChatGPT account data export under **Settings → Data controls → Export data**; preserve the original separately and import the ZIP into the migration workspace for project-specific filtering. Check what audio, if any, actually appears in that account archive instead of assuming audio is included.

**Current source files and Git history** — The attached baseline repository is a verified 2026-10-26 snapshot, not today's head. Run `scripts/fetch_pinned_github_source.sh` on an internet-connected destination. If full branches/history are required, mirror-clone the repository and preserve it as a Git bundle from a trusted environment, separately from this package. Confirm the intended branch before merging PR #2.

**Whimsical original repository** — The GitHub URL previously supplied is inaccessible (404), so only the saved images, marketing decisions and related local packages are retained. Recover the correct repository URL/permissions or upload the source archive.

**Cloud deployments/databases** — Render did not expose running services, Postgres or Key Value in the confirmed workspace. Check other accounts/workspaces and collect infrastructure-as-code, service definitions, database backups and deployment history, if they exist. Cloud secret values must NOT go into this portable export.

**External Claude sessions and other tools** — Historical Claude sessions, external chats and complete issue/PR comments are not contained in the baseline. Export through those platforms if needed.

**Intellectual-property and founder documentation** — Review archived founder/provenance docs for personally sensitive content and validate which material is a dated conception, prototype, tested evidence, or filed/protected IP. Do not assume legal status from file names or ™ notation.

**Safety** — Keep `ROBINHOOD_MCP_MODE=paper`, disable `AUTO_START_TRADING` and `AUTO_START_MONITOR`, do not include account credentials, and do not run the archived bot until after an explicit paper-safety verification. The export includes code and history but does not perform a live-trading safety audit or certification.
