"""Doc research via Browserbase Search + Fetch. Scraping only — ticket updates go through the GitHub API.

Search and Fetch are plain API calls: no browser session or LLM is needed to find and read docs.
Internal docs are searched with `site:` queries, so the internal docs sites must be publicly reachable.
"""

import asyncio
import logging
import re
from typing import Protocol

from browserbase import AsyncBrowserbase
from pydantic import BaseModel

log = logging.getLogger(__name__)

# A line made only of markdown links/images (nav bars, breadcrumbs, footers).
_LINK_ONLY_LINE = re.compile(r"[\s\-*\d.]*(!?\[[^\]]*\]\([^)]*\)\s*)+")


class Page(BaseModel):
    url: str
    title: str
    text: str


class Browserbase(Protocol):
    async def search_and_fetch(self, query: str) -> list[Page]: ...


def main_content(markdown: str) -> str:
    """Drop navigation before the first heading and link-only lines, so Jev sees the page body."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("# ")), 0)
    return "\n".join(l for l in lines[start:] if l.strip() and not _LINK_ONLY_LINE.fullmatch(l))


class RealBrowserbase:
    def __init__(self, api_key: str, results_per_query: int) -> None:
        self._client = AsyncBrowserbase(api_key=api_key)
        self._results = results_per_query

    async def _fetch(self, url: str, title: str) -> Page | None:
        try:
            page = await self._client.fetch_api.create(url=url, format="markdown")
        except Exception as e:
            # One unreadable page (e.g. a PDF Browserbase can't convert) shouldn't sink the whole search.
            log.warning("browserbase fetch skipped %s: %s", url, e)
            return None
        if page.status_code != 200 or not isinstance(page.content, str):
            log.warning("browserbase fetch skipped %s: status %s", url, page.status_code)
            return None
        text = main_content(page.content)
        return Page(url=url, title=title or url, text=text) if text else None

    async def search_and_fetch(self, query: str) -> list[Page]:
        results = await self._client.search.web(query=query, num_results=self._results)
        pages = await asyncio.gather(*(self._fetch(r.url, r.title) for r in results.results))
        return [p for p in pages if p]


class FakeBrowserbase:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search_and_fetch(self, query: str) -> list[Page]:
        self.queries.append(query)
        return [Page(url=f"https://example.invalid/fake?q={len(self.queries)}", title=f"[fake] {query}", text=f"[fake page for] {query}")]
