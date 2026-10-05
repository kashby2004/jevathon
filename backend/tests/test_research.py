"""Research stage on fakes: Jev's topic picks drive the Browserbase queries."""

import asyncio

from app.config import Settings
from app.models import Ticket
from app.services.browserbase import FakeBrowserbase, main_content
from app.services.jev import FakeJev, noul
from app.services.llm import FakeLLM
from app.stages.research import research


def test_jev_topics_become_internal_and_external_queries():
    cfg = Settings(_env_file=None, internal_docs_sites="docs.example.com", research_max_topics=2)
    jev = FakeJev({"caching": noul(0.9), "privacy_compliance": noul(0.7), "capacity": noul(0.6),
                   "not_an_incident": noul(0.99), "rel_0": noul(0.9)})
    browser = FakeBrowserbase()
    t = Ticket(issue_number=1, title="Customer PII visible to other tenants", body="", topic="backend")

    out = asyncio.run(research(t, FakeLLM(), browser, jev, cfg))

    assert browser.queries == [
        "site:docs.example.com Cache key design and cache invalidation in backend",
        "Cache key design and cache invalidation in backend: Customer PII visible to other tenants",
        "site:docs.example.com Privacy, compliance, and breach notification obligations (GDPR etc.)",
        "Privacy, compliance, and breach notification obligations (GDPR etc.): Customer PII visible to other tenants",
    ]  # capped at 2 topics; the routing-only "not_an_incident" topic is never searched
    assert out.startswith("[fake-llm]")


def test_no_topic_above_threshold_searches_the_title():
    browser = FakeBrowserbase()
    t = Ticket(issue_number=2, title="Checkout 500s", body="")
    out = asyncio.run(research(t, FakeLLM(), browser, FakeJev(), Settings(_env_file=None)))
    assert browser.queries == ["Checkout 500s: Checkout 500s"]
    assert "searched the issue title" in out


def test_main_content_drops_navigation():
    page = "[Skip to content](#main)\n* [Home](/)\n# Title\nBody text with a [link](/x).\n[Next](/n)"
    assert main_content(page) == "# Title\nBody text with a [link](/x)."
