"""Minimal examples of calling Jev (TypeSafe's System One model) from Python.

Setup:
    pip install -r requirements.txt
    export TYPESAFE_API_KEY=...   # or put it in backend/.env

Run:
    python jev_client.py "I was charged twice. Please fix this ASAP."
"""

import json
import os
import sys

import truststore

# Use the OS certificate store so HTTPS works behind corporate TLS-inspecting proxies.
truststore.inject_into_ssl()

import requests
from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

load_dotenv()

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"


def ask_jev(text: str) -> dict:
    """Ask Jev three typed questions about `text` using the official SDK."""
    with TypeSafeClient() as client:  # reads TYPESAFE_API_KEY from the environment
        response = client.system_one(
            state={"document": text},
            questions={
                "billing": Noul(instructions="Is `document` about billing?"),
                "tone": Choice(
                    instructions="What is the customer's tone in `document`?",
                    criteria={"calm": None, "frustrated": None, "angry": None},
                ),
                "urgency": Score(
                    instructions="How urgent is the request in `document`?",
                    criteria=["can wait", "this week", "today"],
                ),
            },
        )

    tone = response.choices["tone"]
    urgency = response.scores["urgency"]
    return {
        "billing": response.nouls["billing"].noul,
        "tone": {
            "choice": tone.choice,
            "probabilities": getattr(tone, "probabilities", None),
            "confidence": getattr(tone, "confidence", None),
        },
        "urgency": {
            "score": urgency.score,
            "confidence": getattr(urgency, "confidence", None),
        },
    }


def ask_jev_http(text: str) -> dict:
    """Same request via the raw HTTP API, without the SDK."""
    payload = {
        "model": MODEL,
        "state": {"document": text},
        "questions": {
            "billing": {"type": "noul", "instructions": "Is `document` about billing?"},
            "tone": {
                "type": "choice",
                "instructions": "What is the customer's tone in `document`?",
                "criteria": {"calm": None, "frustrated": None, "angry": None},
            },
            "urgency": {
                "type": "score",
                "instructions": "How urgent is the request in `document`?",
                "criteria": ["can wait", "this week", "today"],
            },
        },
    }
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY is not set. Export it or add it to backend/.env.")

    text = " ".join(sys.argv[1:]) or "I was charged twice. Please fix this ASAP."
    print("SDK result:")
    print(json.dumps(ask_jev(text), indent=2))
    print("\nHTTP result:")
    print(json.dumps(ask_jev_http(text), indent=2))
