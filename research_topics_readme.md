# Research Topics for On-Call Issues (Jev)

For each on-call ticket, Jev suggests which **research topics** are worth investigating. The resulting list is meant to be passed to another LLM for the actual research.

## How it works

Jev doesn't generate free text. It returns typed judgments. So we use TypeSafe's *select instead of generate* pattern:

1. `backend/data/research_topics.json` holds a catalog of **26 candidate topics**, each with:
   - `id`: used by code
   - `question`: the yes/no judgment Jev makes about the ticket
   - `topic`: the text that goes in the output; `{service}` is filled from the ticket
2. For each ticket, `backend/research_topics.py` sends Jev **only `issue` and `context`** (never the severity label). It asks one **Noul** (probability of yes) per topic, all 26 in a **single request**; they're evaluated in parallel.
3. Topics with probability ≥ 0.5 are kept, sorted by probability, and capped at 6.

The team can add, remove, or reword topics by editing the JSON catalog; no code changes are needed.

**Topic coverage:** recent deploys/rollback, config and feature flags, database health/failover, capacity, performance/indexes, caching, third-party vendors, cloud provider, certificates/secrets, auth/SSO, network/DNS/CDN/WAF, rate limiting, queues/workers, scheduled jobs, date/timezone bugs, ETL/schema, data integrity, backup/restore, security forensics, privacy/compliance, billing remediation, mobile releases, customer comms, enterprise accounts, observability gaps, and "not an incident, re-route."

## Run it

```sh
cd backend
export TYPESAFE_API_KEY=...          # or use backend/.env
.venv/bin/python research_topics.py                        # all tickets
.venv/bin/python research_topics.py INC-1001 INC-1003      # specific tickets
.venv/bin/python research_topics.py --threshold 0.6 --max 5
```

Results print to the terminal and are saved to `backend/output/research_topics.json` (gitignored), ready to send to the other LLM:

```json
{
  "INC-1003": {
    "issue": "Customer PII visible to other tenants",
    "research_topics": [
      {"id": "caching", "topic": "Cache key design and cache invalidation in tenant-dashboard", "probability": 0.96},
      {"id": "privacy_compliance", "topic": "Privacy, compliance, and breach notification obligations (GDPR etc.)", "probability": 0.91},
      {"id": "security_breach", "topic": "Security incident response and forensics for tenant-dashboard", "probability": 0.84}
    ]
  }
}
```

## First run on the 32 sample tickets

Most suggestions were on target:

| Ticket | Top topics Jev picked |
|---|---|
| INC-1005 Ransomware | Security forensics (0.98), backup/restore (0.95), data integrity (0.94), compliance (0.80) |
| INC-1006 "Minor blip" (double-billing) | Data integrity (0.96), billing refunds (0.94), deploys (0.93), retries (0.92) |
| INC-1008 Job deleting accounts | Scheduled-job safeguards (0.98), timezone bugs (0.98), data integrity (0.97), backups (0.92) |
| INC-1011 iOS push down | Cert/key rotation (0.99), mobile release (0.99) |
| INC-1013 Mastercard failures | Third-party vendor status (0.97) |
| INC-1024 SSO for one customer | Cert rotation (0.99), SSO config (0.99), enterprise escalation (0.91) |
| INC-1025 Pricing typo | Re-route: not an incident (0.52) |

**Weak spots to tune:**
- **"Recent deploys" is a catch-all.** It shows up for almost every ticket, even cosmetic ones. Consider tightening its question to "is there evidence in the context of a recent deploy?"
- **Some borderline false positives**, e.g. privacy/compliance at 0.53–0.54 for the Mastercard and Android crash tickets. Raising `--threshold` to 0.6 removes these.
- **The "not an incident" topic is weak.** It scored only about 0.5 on cosmetic tickets and didn't fire at all for INC-1031 (a feature request misfiled as an incident). Rewording that question, or making it a separate Noul checked before topic selection, would help.

## Cost

One API call per ticket, containing 26 Noul questions.
