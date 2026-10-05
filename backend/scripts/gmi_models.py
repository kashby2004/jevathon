"""List the Claude and OpenAI models on GMI and check which ones this key can actually call.

GMI lists models the org may still be rate-limited on (Claude returned 429 at the hackathon), so each
candidate gets a one-line test request. Put a working one in backend/.env as AGENT_MODEL.

Run from backend/:
    .venv/bin/python scripts/gmi_models.py            # Claude + OpenAI models
    .venv/bin/python scripts/gmi_models.py gemini glm # other name filters
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import APIStatusError, AsyncOpenAI

from app.config import Settings


async def probe(client: AsyncOpenAI, model: str) -> str:
    try:
        r = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Reply with the word ok."}], max_tokens=16
        )
    except APIStatusError as e:
        return f"HTTP {e.status_code}"
    except Exception as e:
        return f"error: {type(e).__name__}"
    return "OK" if r.choices and r.choices[0].message.content else "empty reply"


async def main() -> None:
    cfg = Settings()
    if not cfg.gmi_api_key:
        sys.exit("GMI_API_KEY is not set. Add it to backend/.env.")
    filters = [f.lower() for f in sys.argv[1:]] or ["claude", "anthropic", "openai", "gpt"]

    client = AsyncOpenAI(api_key=cfg.gmi_api_key.get_secret_value(), base_url=cfg.gmi_base_url)
    models = sorted(m.id for m in (await client.models.list()).data if any(f in m.id.lower() for f in filters))
    if not models:
        sys.exit(f"No GMI models match {filters}.")

    results = await asyncio.gather(*(probe(client, m) for m in models))
    for model, status in zip(models, results):
        print(f"{status:<12} {model}")


if __name__ == "__main__":
    asyncio.run(main())
