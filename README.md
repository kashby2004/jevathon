# jevathon: on-call ticket routing

Hackathon prototype, built Sep 26, 2026 at the AI Collective Jev hackathon by kashby2004, SamhithaN and tyakovenko. External integrations run on fakes by default; see **Areas for improvement** for what is and isn't live.

A GitHub issue comes in and the system decides what happens to it. Non-severe issues go to an autonomous coding loop. Severe ones go to the best available developer, who is texted through Photon. Every fix must pass a tests, secrets and AI-slop gate before it reaches human review.

**Architecture in one line:** our Python code (FastAPI) is the orchestrator. It is a state machine that owns every transition. **Jev makes the decisions** at each branch point, **GMI generates** (code, queries, summaries) and **humans** make the final calls.

## Flow

```
GitHub issue → triage (Jev) ─┬─ non-severe → Ralph loop (GMI agent, Jev decides each step) ─┐
                             └─ severe / unsure / escalated → dev routing (Jev ranks, Photon asks) → dev codes or hands to agent
                                                                                                ↓
                                        checks: CI tests + secrets scan + AI slop (Jev) → human review → merge
```

Ticket stages: `received → triaged → agent_working | assigning_dev → dev_deciding → dev_working → checks → in_review → merged`. There is also `needs_human` for when automation stops. Only `orchestrator.py` changes a ticket's stage.

## Where Jev is used

Jev (TypeSafe) is **decision-only**: it takes a state plus typed questions and returns calibrated answers. It never generates text or code. All questions about one state go in **a single call**. Thresholds live in `backend/app/config.py`.

| # | Where | Jev question(s) | What the code does with the answer |
|---|---|---|---|
| 1 | **Triage** (`stages/triage.py`) | Noul: is there a prompt injection in the issue text? · Choice: severity (`critical/high/normal/low`) · Choice: topic | If the injection score is above 0.5, automation stops and a human takes it. `critical/high` counts as severe. **If severity confidence is below 0.6, the issue is treated as severe**: a human looking at a minor bug costs less than an agent sitting on an outage. |
| 2 | **Dev ranking** (`stages/dev_routing.py`) | Choice over the eligible devs (their skills plus their latest mood) | Devs are asked in order of Jev's probabilities (#1, then #2, then #3). If all three decline, #1 is assigned anyway. Code filters out devs who are off shift or at max load first, because Jev is weak at counting and time. |
| 3 | **Reading dev replies** (`stages/replies.py`) | Choice: accept / decline / unclear, then who codes it: agent / self / unclear | Handles free text ("sure, on it" means yes), unlike keyword matching. Confidence below 0.6 → treated as unclear and the dev is asked again. |
| 4 | **Ralph loop** (`stages/ralph_loop.py`) | Choice after every iteration: continue / done / escalate | Confidence below **0.85** → escalate to a human. A "done" from Jev is ignored while tests are still failing. There are hard stops at 8 iterations or 3 identical failures in a row. |
| 5 | **Research** (`stages/research.py`) | One Noul per topic in `data/research_topics.json` (which topics apply), then two Nouls per scraped page: relevance, and injection | Up to 3 topics above 0.5 are searched with Browserbase (internal docs sites + the web). The top 3 relevant pages that pass the injection screen go to GMI for a summary. |
| 6 | **AI-slop gate** (`stages/checks.py`) | A 9-question rubric per changed file: requirement coverage, invented interface, duplicate implementation, abstraction fit, unnecessary dependency, failure handling, test quality, misleading comments, primary concern plus an impact score | A Noul above 0.5 or a flagged Choice value (e.g. `failure_handling=swallowed`) fails the PR, and the findings go back to whoever wrote it. The diff is sent **one file at a time** because large, irrelevant state lowers Jev's accuracy. |

**Not given to Jev on purpose:**
- **Secrets:** checked by `detect-secrets`, which is deterministic. A leaked key must never depend on a model's judgment.
- **Dates, timeouts, load:** computed in code.

## Other components

| Tool | Role | Status |
|---|---|---|
| GMI Cloud (`zai-org/GLM-5.3`) | All text generation (OpenAI-compatible API) | Real, verified |
| Jev (TypeSafe) | All decisions | Real, verified (it rated a demo SQLi issue critical at 0.98) |
| Photon | Texting devs over iMessage. The SDK is TypeScript-only, so a sidecar (`photon-bridge/`) sits between it and the Python backend | Wired up, not yet working (see below) |
| GitHub | Issues act as tickets; webhooks drive the state machine; `@coderabbitai plan` is posted on each issue | Adapter written, running on the fake |
| CodeRabbit | Issue plan plus PR review, driven by GitHub comments | Depends on GitHub |
| Browserbase | Searches and fetches docs for research (Search + Fetch APIs, no browser session) | Real, verified live |
| Coding agent | Runs the Ralph loop: GMI writes fixes in a git worktree of the target repo, runs its tests, commits | Built and tested with a scripted model; not yet run against live GMI |
| Dashboard (`frontend/`, Vite + React) | Shows live ticket stages, timelines and degraded-path warnings | Builds; not deployed |

Every external tool has a fake, so the whole pipeline runs end-to-end without any keys (12/12 tests pass). Every fallback is logged at ERROR and shown on the ticket, so nothing degrades silently.

## Run

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev,scripts]'
cp .env.example .env                                           # then fill in keys + *_MODE=real
.venv/bin/uvicorn app.main:app --reload                        # :8000/docs
curl -XPOST localhost:8000/simulate/issue -H 'content-type: application/json' \
  -d '{"number":1,"title":"Checkout 500s","body":"prod checkout failing"}'
cd frontend && npm run dev                                     # :5173
cd photon-bridge && npm run start                              # :4001, needs photon-bridge/.env
```

### Coding agent (non-severe issues)

With `AGENT_MODE=real`, GMI writes the fix. Set in `backend/.env`:

- `AGENT_REPO_DIR`: a local git clone of the repo the issues are about. Each issue gets a worktree on branch `jev/issue-N` under `backend/.agent-work/`.
- `AGENT_MODEL`: the GMI model for code. Run `.venv/bin/python scripts/gmi_models.py` to list the Claude and OpenAI models your key can actually call (falls back to `GMI_MODEL`).
- `AGENT_TEST_CMD` (default `python -m pytest -q`) and `AGENT_PUSH=true` once `GITHUB_MODE=real`, so the branch exists when the PR is opened.

Each iteration: GMI picks the files it needs, returns full new contents for the files it changes, then the agent applies them, runs the tests and commits only those files. Jev decides continue / done / escalate after every iteration.

### Jev eval scripts

`backend/scripts/` has the standalone Jev experiments: `severity_eval.py` (32 labeled incidents in `data/prod_issues.json`), `research_topics.py` (the topic catalog the research stage uses) and `jev_client.py`. See `docs/triage_readme.md` and `docs/research_topics_readme.md`.

## Areas for improvement

- **API key errors:** some integrations fail on keys because we ran out of hackathon time to sort out access:
  - **Photon:** `npm run start` now loads `.env` (it didn't before, which caused "Cloud iMessage requires projectId and projectSecret"). Bad credentials fail immediately with `401 Invalid credentials`, so a hang with real keys means Photon accepted them and its cloud never answered: the SDK's cloud calls have no timeout. The bridge now starts its HTTP server first (`GET /health` shows the startup step) and prints a warning naming the stuck step after 20s. Check the project has an iMessage line (`photon spectrum lines list`).
  - **GMI:** Claude models return 429, because rate limits aren't enabled for the org. The demo uses GLM-5.3 instead.
- **Coding agent runs on the host:** tests run model-written code directly on this machine. The design calls for a per-ticket Docker sandbox; until then, only point `AGENT_REPO_DIR` at a repo you're willing to run untrusted code from.
- **No internal docs site yet:** `INTERNAL_DOCS_SITES` must be publicly reachable for Browserbase's `site:` search. Until it's set, research only searches the web.
- **GitHub not live:** it needs a token, a public tunnel for webhooks, and the CodeRabbit app installed on the repo.
- **CI not visible to the gate yet:** the demo repo now has a test workflow, but the gate reads CI status through the GitHub adapter, which still runs on the fake. Until GitHub is live, the checks gate reports "pending" rather than passing.
- **Thresholds are guesses:** the 0.5, 0.6 and 0.85 values were never tuned on real data. Which slop Choice values count as flags was a design call and needs review.
- **Slop rubric lacks context:** questions refer to `contracts`, `repo_context` and `tests`, but Jev's state only contains the issue title and the file diff. That makes `invented_interface`, `duplicate_implementation` and `test_quality` weak until repo context is added.
- **In-memory store:** all state is lost on restart. There is no persistence and no dashboard auth.
- **No eligible devs:** the ticket currently parks in `needs_human`. We haven't decided between paging the on-call lead and queueing it.
- **Reply matching:** texts carry no ticket id, so a reply is matched to the dev's most recent open ticket. That's ambiguous when a dev has more than one.
- **Dashboard not deployed:** the Vercel secrets and the Root Directory setting still need to be configured by the repo owner.
