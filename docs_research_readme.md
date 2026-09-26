# Docs Research for On-Call Issues (Browserbase)

After Jev picks **research topics** for a ticket (see `research_topics_readme.md`), Browserbase finds docs for each topic. The docs go back to Jev, which judges which ones are best to send to the dev.

## How it works

`backend/docs_research.py` reads `backend/output/research_topics.json` and, for each topic:

1. **Internal docs:** searches each site in `INTERNAL_DOCS_SITES` with a `site:` query on the topic.
2. **External research:** searches the open web for the topic plus the ticket's issue.
3. **Fetches** every hit as markdown through Browserbase and trims it to a ~1500-character excerpt of the main content.

Duplicate URLs are kept once, under the highest-probability topic. Pages that can't be converted to markdown (e.g. PDFs) are skipped. This uses Browserbase Search and Fetch, so no browser session or LLM is needed.

## Run it

```sh
cd backend
# backend/.env needs:
#   BROWSERBASE_API_KEY=...
#   INTERNAL_DOCS_SITES=docs.ourcompany.com,wiki.ourcompany.com   # optional, comma-separated
../.venv/bin/python research_topics.py INC-1003    # step 1: Jev picks topics
../.venv/bin/python docs_research.py INC-1003      # step 2: Browserbase finds docs
../.venv/bin/python docs_research.py --results 2   # fewer hits per topic, all tickets
```

Output goes to `backend/output/research_docs.json` (gitignored). Each doc has an `id` so Jev can refer to it:

```json
{
  "INC-1003": {
    "issue": "Customer PII visible to other tenants",
    "docs": [
      {
        "id": "doc1",
        "topic_id": "caching",
        "topic": "Cache key design and cache invalidation in tenant-dashboard",
        "source": "external",
        "title": "Cache keys include the tenant identifier",
        "url": "https://...",
        "excerpt": "# Cache keys include the tenant identifier\n..."
      }
    ]
  }
}
```

From other code, call `await research_ticket(issue, research_topics)` to get the same `docs` list for one ticket.

## Next step for Jev

To pick the best docs, ask Jev one Noul per doc (e.g. "Would the doc in `doc` help the on-call engineer fix the incident in `issue`?"), with the ticket and the doc's `title` + `excerpt` as state. Keep the top few.

## Open question

**There is no internal docs site yet.** `INTERNAL_DOCS_SITES` must be publicly reachable for Browserbase to search it. For the demo, we could publish a few sample runbooks (e.g. as pages on our Vercel site) and point this at that domain.
