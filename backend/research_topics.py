"""Ask Jev which research topics are worth investigating for each on-call issue.

Jev doesn't generate text, so it selects from a catalog of candidate topics
(data/research_topics.json): one Noul per topic, all in a single request.
Only `issue` and `context` are sent to Jev.

Run:
    python research_topics.py                     # all tickets
    python research_topics.py INC-1001 INC-1003   # specific tickets
    python research_topics.py --threshold 0.6 --max 5
"""

import argparse
import json
import os
import sys
from pathlib import Path

import truststore

# Use the OS certificate store so HTTPS works behind corporate TLS-inspecting proxies.
truststore.inject_into_ssl()

from dotenv import load_dotenv
from typesafe_sdk import Noul, TypeSafeClient

load_dotenv()

BASE_DIR = Path(__file__).parent
TICKETS_PATH = BASE_DIR / "data" / "prod_issues.json"
CATALOG_PATH = BASE_DIR / "data" / "research_topics.json"
OUTPUT_PATH = BASE_DIR / "output" / "research_topics.json"


def topic_questions(catalog: list[dict]) -> dict[str, Noul]:
    return {
        t["id"]: Noul(
            instructions=(
                "Consider the on-call incident described in `issue` and `context`. "
                f"{t['question']} Answer yes only if researching this would help the "
                "on-call engineer diagnose, fix, or respond to this specific incident."
            )
        )
        for t in catalog
    }


def suggest_topics(
    client: TypeSafeClient,
    ticket: dict,
    catalog: list[dict],
    threshold: float = 0.5,
    max_topics: int = 6,
) -> list[dict]:
    response = client.system_one(
        state={"issue": ticket["issue"], "context": ticket["context"]},
        questions=topic_questions(catalog),
    )
    service = ticket["context"].get("service", "the affected service")
    ranked = sorted(
        (
            {"id": t["id"], "topic": t["topic"].format(service=service), "probability": response.nouls[t["id"]].noul}
            for t in catalog
        ),
        key=lambda r: r["probability"],
        reverse=True,
    )
    return [r for r in ranked if r["probability"] >= threshold][:max_topics]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_ids", nargs="*", help="Ticket IDs to process (default: all)")
    parser.add_argument("--threshold", type=float, default=0.5, help="Minimum probability to include a topic")
    parser.add_argument("--max", type=int, default=6, help="Maximum topics per ticket")
    args = parser.parse_args()

    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY is not set. Export it or add it to backend/.env.")

    tickets = json.loads(TICKETS_PATH.read_text())["tickets"]
    catalog = json.loads(CATALOG_PATH.read_text())["topics"]
    if args.ticket_ids:
        wanted = set(args.ticket_ids)
        tickets = [t for t in tickets if t["id"] in wanted]
        if missing := wanted - {t["id"] for t in tickets}:
            sys.exit(f"Unknown ticket IDs: {', '.join(sorted(missing))}")

    results = {}
    with TypeSafeClient() as client:
        for ticket in tickets:
            topics = suggest_topics(client, ticket, catalog, args.threshold, args.max)
            results[ticket["id"]] = {"issue": ticket["issue"], "research_topics": topics}
            print(f"\n{ticket['id']}: {ticket['issue']}")
            for t in topics or [{"topic": "(no topics above threshold)", "probability": 0.0}]:
                print(f"  {t['probability']:.2f}  {t['topic']}")

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUTPUT_PATH.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
