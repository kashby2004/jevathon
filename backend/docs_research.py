"""Research the topics Jev picked for each on-call issue, using Browserbase.

For every research topic (output of research_topics.py), Browserbase searches:
  - internal docs: the sites in INTERNAL_DOCS_SITES (via `site:` search)
  - external research: the open web
and fetches each hit as markdown. The result is a list of candidate docs per
ticket that Jev can judge to pick the best ones to send to the dev.

Run (after research_topics.py):
    python docs_research.py                     # all tickets in output/research_topics.json
    python docs_research.py INC-1001 INC-1003   # specific tickets
    python docs_research.py --results 2         # fewer search hits per topic
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import truststore

# Use the OS certificate store so HTTPS works behind corporate TLS-inspecting proxies.
truststore.inject_into_ssl()

from dotenv import load_dotenv
from stagehand import browserbase

load_dotenv()

BASE_DIR = Path(__file__).parent
TOPICS_PATH = BASE_DIR / "output" / "research_topics.json"
OUTPUT_PATH = BASE_DIR / "output" / "research_docs.json"

EXCERPT_CHARS = 1500
MAX_CONCURRENT_FETCHES = 8


def internal_sites() -> list[str]:
    return [s.strip() for s in os.getenv("INTERNAL_DOCS_SITES", "").split(",") if s.strip()]


def excerpt(markdown: str) -> str:
    """Trim a fetched page to its main content: skip nav/link-only lines, cap the length."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("# ")), 0)
    kept = [
        line for line in lines[start:]
        if line.strip() and not re.fullmatch(r"[\s\-*\d.]*(!?\[[^\]]*\]\([^)]*\)\s*)+", line)
    ]
    return "\n".join(kept)[:EXCERPT_CHARS]


async def search(query: str, num_results: int) -> list[dict]:
    try:
        result = await browserbase.search(
            api_key=os.environ["BROWSERBASE_API_KEY"], query=query, num_results=num_results
        )
    except Exception as e:
        print(f"  search failed for {query!r}: {e}", file=sys.stderr)
        return []
    return [{"title": r.title, "url": r.url} for r in result.results]


async def fetch_excerpt(url: str, limit: asyncio.Semaphore) -> str | None:
    async with limit:
        try:
            page = await browserbase.fetch(
                api_key=os.environ["BROWSERBASE_API_KEY"], url=url, format="markdown"
            )
        except Exception:
            # e.g. PDFs, which Browserbase can't convert to markdown
            print(f"  skipped {url} (couldn't fetch as markdown)", file=sys.stderr)
            return None
    if page.status_code != 200 or not isinstance(page.content, str):
        return None
    return excerpt(page.content) or None


async def research_ticket(issue: str, topics: list[dict], num_results: int = 3) -> list[dict]:
    """Find and fetch candidate docs for one ticket's research topics."""
    searches = []  # (topic, source, query)
    for topic in topics:
        for site in internal_sites():
            searches.append((topic, "internal", f"site:{site} {topic['topic']}"))
        searches.append((topic, "external", f"{topic['topic']}: {issue}"))

    hits = await asyncio.gather(*(search(query, num_results) for _, _, query in searches))

    # Keep the first (highest-probability topic) occurrence of each URL.
    candidates, seen = [], set()
    for (topic, source, _), results in zip(searches, hits):
        for hit in results:
            if hit["url"] not in seen:
                seen.add(hit["url"])
                candidates.append({"topic_id": topic["id"], "topic": topic["topic"], "source": source, **hit})

    limit = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    excerpts = await asyncio.gather(*(fetch_excerpt(c["url"], limit) for c in candidates))
    docs = [{**c, "excerpt": text} for c, text in zip(candidates, excerpts) if text]
    return [{"id": f"doc{i}", **doc} for i, doc in enumerate(docs, 1)]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_ids", nargs="*", help="Ticket IDs to process (default: all)")
    parser.add_argument("--results", type=int, default=3, help="Search hits per topic per source")
    args = parser.parse_args()

    if not os.environ.get("BROWSERBASE_API_KEY"):
        sys.exit("BROWSERBASE_API_KEY is not set. Export it or add it to backend/.env.")
    if not TOPICS_PATH.exists():
        sys.exit(f"{TOPICS_PATH.relative_to(BASE_DIR)} not found. Run research_topics.py first.")
    if not internal_sites():
        print("INTERNAL_DOCS_SITES is not set; searching external research only.\n")

    tickets = json.loads(TOPICS_PATH.read_text())
    if args.ticket_ids:
        if missing := set(args.ticket_ids) - tickets.keys():
            sys.exit(f"Unknown ticket IDs: {', '.join(sorted(missing))}")
        tickets = {tid: tickets[tid] for tid in args.ticket_ids}

    results = {}
    for ticket_id, ticket in tickets.items():
        docs = await research_ticket(ticket["issue"], ticket["research_topics"], args.results)
        results[ticket_id] = {"issue": ticket["issue"], "docs": docs}
        print(f"\n{ticket_id}: {ticket['issue']}")
        for doc in docs or [{"source": "-", "title": "(no docs found)", "url": ""}]:
            print(f"  [{doc['source']}] {doc['title'][:70]}  {doc['url']}")

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUTPUT_PATH.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    asyncio.run(main())
