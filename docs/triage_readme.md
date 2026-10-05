# Jevathon: On-Call Ticket Triage with Jev

We're building an on-call ticket routing app. The first step is to have **Jev** (TypeSafe AI's System One model) triage production incidents using only the ticket's issue description and context:
- **Severity:** how bad is it (SEV1–SEV4)?
- **Research topics:** what should be investigated? This list will be handed to another LLM.

This README covers what has been built so far, how to run it, and what the first evaluation showed.

## What's in the repo

```
backend/
  scripts/jev_client.py       # Minimal examples of calling Jev (SDK + raw HTTP)
  scripts/severity_eval.py    # Rates every ticket's severity with Jev and scores the results
  scripts/research_topics.py  # Picks relevant research topics per ticket with Jev
  data/prod_issues.json  # 32 labeled, synthetic production incidents
  data/research_topics.json  # Editable catalog of 26 candidate research topics
  output/                # Generated results (gitignored)
  pyproject.toml         # install with: pip install -e '.[scripts]'
  .env.example           # Template for the API key
frontend/                # React + Vite + Tailwind app (not yet wired to the backend)
skills/typesafe-ai/      # Claude Code skill with TypeSafe/Jev guidance
triage_readme.md         # This file
research_topics_readme.md  # Details and results for the research topics feature
```

## What we did

### 1. Connected to Jev from Python
`backend/jev_client.py` shows two ways to call Jev:
- **Python SDK** (`typesafe-sdk`): `TypeSafeClient().system_one(state=..., questions=...)`
- **Raw HTTP**: `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer <key>` and `"model": "jev-latest"`

Both ask the three question types Jev supports:
- **Noul**: probability that a yes/no condition holds
- **Choice**: pick one option from a set
- **Score**: position on an ordered scale

Both returned matching answers from model `jev-1.13.0`.

**Corporate network fix:** the NiCE network inspects HTTPS traffic and re-signs it with its own certificate (`Panorama-Root-CA-2023-1`), so the raw `requests` call failed with `CERTIFICATE_VERIFY_FAILED`. We added [`truststore`](https://pypi.org/project/truststore/) so Python uses the macOS keychain, which already trusts that certificate. Certificate verification stays **on**.

### 2. Created a labeled incident dataset
`backend/data/prod_issues.json` contains 32 realistic, **synthetic** incidents, 8 for each severity:

| Level | Meaning |
|---|---|
| SEV1 | Critical: full outage, data loss, or security breach affecting many customers; no workaround |
| SEV2 | High: major feature down/degraded for many users, or a key customer blocked |
| SEV3 | Medium: partial degradation, limited users, or a workaround exists |
| SEV4 | Low: cosmetic, internal-only, or a single user with an easy workaround |

Each ticket has an `id`, an `issue` title, a `context` object (service, environment, affected users, metrics, start time, workaround, reporter, details), and the ground-truth `severity`.

Some tickets are deliberately tricky, to check that Jev judges **impact rather than tone**. For example, "Minor blip on dashboard, probably nothing" is really customers being double-billed, and "EVERYTHING IS BROKEN" is really a misaligned tooltip.

### 3. Evaluated Jev on severity
`backend/severity_eval.py` sends Jev **only `issue` and `context`**; the label is never sent. It asks a single **Score** question on the SEV4 → SEV1 scale, rounds the score to a level, and compares it with the label.

**Results (first run):**

| Metric | Result |
|---|---|
| Exact match | **28 / 32 (88%)** |
| Within one level | **32 / 32 (100%)** |

```
Confusion matrix (rows = expected, cols = Jev)
         SEV1  SEV2  SEV3  SEV4
SEV1        6     2     0     0
SEV2        0     8     0     0
SEV3        0     0     7     1
SEV4        0     0     1     7
```

It handled the misleading tickets well: the "Minor blip" double-billing was rated SEV1, and "URGENT!!! Profile pictures" was rated SEV3.

**The 4 misses:**

| Ticket | Label | Jev | Likely reason |
|---|---|---|---|
| INC-1004 Login fails for every user | SEV1 | SEV2 | "Users already logged in can continue" read as a workaround |
| INC-1008 Job deleting active accounts | SEV1 | SEV2 | "Job can be disabled" read as a workaround |
| INC-1020 Staging environment down | SEV3 | SEV4 | Debatable label: impact is internal only |
| INC-1029 "EVERYTHING IS BROKEN" (tooltip) | SEV4 | SEV3 | Borderline score (0.56); the alarming title pulled it up |

**Confidence is a useful signal.** Three of the four misses had confidence below 0.8. A routing rule such as *auto-route when confidence ≥ 0.8, otherwise send to a human* would have caught them. It would also send about 4 correct tickets to a human, so the cutoff should be tuned on more data.

### 4. Suggested research topics per incident
`backend/research_topics.py` builds a list of research topics for each ticket. Jev doesn't write text, so it **selects** from the catalog in `backend/data/research_topics.json`. It asks one yes/no (Noul) question per topic, all 26 in a single request per ticket, and again sees only `issue` and `context`. Topics with probability ≥ 0.5 are kept, ranked, and capped at 6. `{service}` in each topic is filled from the ticket. Results are saved to `backend/output/research_topics.json`.

Most picks were on target. For example, the ransomware ticket got security forensics, backup/restore and compliance, and the double-billing ticket got data integrity and billing refunds. Weak spots:
- "Recent deploys" shows up for almost every ticket.
- A few borderline false positives (about 0.53) disappear with `--threshold 0.6`.
- The "not an incident, re-route" topic is weak.

See [`research_topics_readme.md`](research_topics_readme.md) for full details and results.

## How to run

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[scripts]'

# Provide your key; never commit it
cp .env.example .env          # then edit .env
# or: export TYPESAFE_API_KEY=...

.venv/bin/python scripts/jev_client.py "I was charged twice. Please fix this ASAP."
.venv/bin/python scripts/severity_eval.py                 # uses data/prod_issues.json
.venv/bin/python scripts/severity_eval.py my_tickets.json # or your own file
.venv/bin/python scripts/research_topics.py               # research topics for all tickets
.venv/bin/python scripts/research_topics.py INC-1001 --threshold 0.6 --max 5
```

Both scripts make one API call per ticket. A severity call uses about 400 tokens; a research-topics call contains 26 Noul questions.

## Security notes
- The API key is read from `TYPESAFE_API_KEY` and is not stored in any file in the repo. `.env` is gitignored.
- A key was shared in a chat session during development and should be **rotated** at https://console.typesafe.ai/.
- For the web app, call Jev from a backend so the key never reaches the browser.

## Next steps
- Tighten the SEV1 wording, e.g. "ongoing data loss or blocking all new logins is SEV1 even if partially mitigated"
- Add a confidence cutoff and a "needs human review" path to the script
- Test on real (anonymized) historical tickets to tune the cutoff
- Expose a small API (e.g. FastAPI) that the frontend can call to route tickets
- Add more Jev questions for routing, e.g. a Choice for the owning team
- Research topics: require evidence of a deploy for "recent deploys", strengthen the "not an incident" check, and tune the threshold
- Combine severity and research topics into one triage output per ticket
