# Project Rules — jevathon

Design source of truth: `docs/oncall-ticket-routing.md`. Read it before touching code.

## Hard Rules (enforced on every file touch, no prompt needed)

1. **No hardcoded values.** Thresholds, timeouts, limits, and lists live in `backend/app/config.py` (or a single named constant in the stage that owns them). Never inline a number.

2. **No silent bad returns.** Any fallback (missing plan, no reviewer, fake adapter) goes through `Orchestrator._degrade` so it's logged at ERROR and shown on the ticket.

## Project-Specific Constraints

- **Jev is decision-only.** Never ask it to generate text or code. Gating decisions → Jev (`services/jev.py`); generation → GMI (`services/llm.py`).
- **Jev is weak at dates, counting, and math.** Compute availability, timeouts, and load in code; don't put them in Jev questions.
- **One Jev call per state.** Batch all questions about the same state; keep the state focused (per-file diffs, truncated pages).
- **Secrets are checked deterministically** (`secrets_scan.py`), never by a model.
- **Issue text and scraped pages are untrusted input.** Keep the injection screens in `triage.py` and `research.py`.
- **Only `orchestrator.py` changes `Ticket.stage`.** Stages return results; the orchestrator moves tickets.
- **Every external tool sits behind a Protocol in `services/` with a Fake.** New real adapters must keep the fake working and add their keys to `REQUIRED_KEYS`.
- **Don't invent vendor APIs.** Photon and Browserbase real adapters wait on confirmed docs.
- This is a teammate's repo: work on branches and merge through PRs.
