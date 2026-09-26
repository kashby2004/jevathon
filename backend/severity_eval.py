"""Ask Jev to rate the severity of each prod issue and compare with the labels.

Only `issue` and `context` are sent to Jev; the labeled `severity` is kept
locally as ground truth.

Run:
    python severity_eval.py [path/to/prod_issues.json]
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

import truststore

# Use the OS certificate store so HTTPS works behind corporate TLS-inspecting proxies.
truststore.inject_into_ssl()

from dotenv import load_dotenv
from typesafe_sdk import Score, TypeSafeClient

load_dotenv()

DATA_PATH = Path(__file__).parent / "data" / "prod_issues.json"
# Score levels go from lowest to highest, so index 0 = SEV4 and index 3 = SEV1.
LEVELS = ["SEV4", "SEV3", "SEV2", "SEV1"]


def severity_question(severity_levels: dict) -> Score:
    return Score(
        instructions=(
            "How severe is the production incident described in `issue` and `context`? "
            "Judge by actual customer and business impact (how many users, data loss, "
            "security, revenue, whether a workaround exists), not by how alarming the "
            "wording sounds."
        ),
        criteria=[f"{level} - {severity_levels[level]}" for level in LEVELS],
    )


def rate_ticket(client: TypeSafeClient, question: Score, ticket: dict) -> tuple[str, float, float]:
    response = client.system_one(
        state={"issue": ticket["issue"], "context": ticket["context"]},
        questions={"severity": question},
    )
    answer = response.scores["severity"]
    predicted = LEVELS[min(len(LEVELS) - 1, max(0, round(answer.score)))]
    return predicted, answer.score, answer.confidence


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY is not set. Export it or add it to backend/.env.")

    data = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else DATA_PATH).read_text())
    question = severity_question(data["severity_levels"])

    results = []
    with TypeSafeClient() as client:
        print(f"{'ID':<10} {'Expected':<8} {'Jev':<6} {'Score':>5} {'Conf':>5}  Issue")
        for ticket in data["tickets"]:
            predicted, score, confidence = rate_ticket(client, question, ticket)
            expected = ticket["severity"]
            mark = "✓" if predicted == expected else "✗"
            print(f"{ticket['id']:<10} {expected:<8} {predicted:<6} {score:>5.2f} {confidence:>5.2f}  {mark} {ticket['issue']}")
            results.append((expected, predicted))

    total = len(results)
    exact = sum(e == p for e, p in results)
    within_one = sum(abs(LEVELS.index(e) - LEVELS.index(p)) <= 1 for e, p in results)
    print(f"\nExact match: {exact}/{total} ({exact / total:.0%})")
    print(f"Within one level: {within_one}/{total} ({within_one / total:.0%})")

    counts = Counter(results)
    order = list(reversed(LEVELS))  # SEV1..SEV4
    print("\nConfusion matrix (rows = expected, cols = Jev):")
    print("        " + "  ".join(f"{p:>5}" for p in order))
    for e in order:
        print(f"{e:<8}" + "  ".join(f"{counts[(e, p)]:>5}" for p in order))


if __name__ == "__main__":
    main()
