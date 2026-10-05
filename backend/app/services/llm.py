"""Text generation via GMI Cloud (OpenAI-compatible). Generation only — gating decisions go to Jev."""

from typing import Protocol

from openai import AsyncOpenAI


class LLM(Protocol):
    async def complete(self, system: str, prompt: str) -> str: ...


class GmiLLM:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(self, system: str, prompt: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError(f"GMI model {self._model} returned empty completion")
        return content


class FakeLLM:
    """Labels its output so fake text can never be mistaken for a real model's."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
        return f"[fake-llm] {first_line[:120]}"
