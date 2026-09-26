"""Browserbase + Stagehand session helper.

Jev uses this for the Browserbase steps in docs/oncall-ticket-routing.md:
docs research (steps 7-9) and clicking through the CodeRabbit UI (steps 14-15).

Run directly as a connection smoke test:
    python backend/browser_session.py
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from stagehand import Page, Stagehand, browserbase

from gmi_llm import gmi_llm

load_dotenv()


@asynccontextmanager
async def browserbase_session() -> AsyncIterator[tuple[Stagehand, Page]]:
    """Launch a Browserbase cloud browser wrapped in Stagehand.

    Yields (stagehand, page). Both are closed on exit so the Browserbase
    session is released instead of running until it times out.
    """
    browser = await browserbase.launch(api_key=os.environ["BROWSERBASE_API_KEY"])
    try:
        # Claude (via GMI) is only needed for act/observe/extract, not plain navigation.
        stagehand = await Stagehand.create(
            browser=browser,
            model=gmi_llm() if os.getenv("GMI_API_KEY") else None,
        )
        try:
            page = (await browser.context.pages())[0]
            yield stagehand, page
        finally:
            await stagehand.close()
    finally:
        # Closing the browser is what releases the Browserbase session.
        await browser.close()


async def main() -> None:
    async with browserbase_session() as (_, page):
        await page.goto("https://ycombinator.com")
        print(f"Successfully connected! Current page title: {await page.title()}")


if __name__ == "__main__":
    asyncio.run(main())
