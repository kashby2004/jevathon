"""Jev (TypeSafe System One). Decisions only — never ask it to generate text or code.

Docs: https://docs.typesafe.ai/api · failure modes: https://docs.typesafe.ai/model-jaggedness/jev-1.13
Batch every question about one state into a single call; they're answered in parallel.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from typesafe_sdk import AsyncTypeSafeClient, Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer

log = logging.getLogger(__name__)

Question = Choice | Noul | Score
Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer


class Jev(Protocol):
    async def ask(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Answer]: ...


class RealJev:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncTypeSafeClient(api_key=api_key, model=model)

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Answer]:
        response = await self._client.system_one(state=state, questions=questions)
        return dict(response.answers)


# A scripted answer: either a fixed Answer or a function of (state, question).
Scripted = Answer | Callable[[Any, Question], Answer]


class FakeJev:
    """Deterministic stand-in. Defaults: Choice → first option @ high confidence, Noul → 0, Score → lowest level.

    Tests and demos override per question id via `script`.
    """

    DEFAULT_CONFIDENCE = 0.95

    def __init__(self, script: dict[str, Scripted] | None = None) -> None:
        self.script: dict[str, Scripted] = script or {}
        self.calls: list[tuple[Any, dict[str, Question]]] = []

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Answer]:
        self.calls.append((state, dict(questions)))
        return {qid: self._answer(qid, state, q) for qid, q in questions.items()}

    def _answer(self, qid: str, state: Any, q: Question) -> Answer:
        scripted = self.script.get(qid)
        if scripted is not None:
            return scripted(state, q) if callable(scripted) else scripted
        if isinstance(q, Choice):
            options = list(q.criteria)
            probs = {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}
            return ChoiceAnswer(type="choice", choice=options[0], confidence=self.DEFAULT_CONFIDENCE, probabilities=probs)
        if isinstance(q, Score):
            levels = {i: (1.0 if i == 0 else 0.0) for i in range(len(q.criteria))}
            legend = {i: str(c) for i, c in enumerate(q.criteria)}
            return ScoreAnswer(type="score", score=0.0, confidence=self.DEFAULT_CONFIDENCE, legend=legend, probabilities=levels)
        return NoulAnswer(type="noul", noul=0.0)


def choice(option: str, confidence: float, options: list[str] | None = None) -> ChoiceAnswer:
    """Helper for scripting FakeJev Choice answers."""
    options = options or [option]
    rest = [o for o in options if o != option]
    spread = (1 - confidence) / len(rest) if rest else 0.0
    probs = {o: (1 - spread * len(rest) if o == option else spread) for o in options}
    return ChoiceAnswer(type="choice", choice=option, confidence=confidence, probabilities=probs)


def noul(p: float) -> NoulAnswer:
    return NoulAnswer(type="noul", noul=p)
