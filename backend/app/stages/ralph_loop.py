"""Step 3a: Ralph loop. The agent codes; Jev picks continue/done/escalate after each iteration (docs §6)."""

from enum import StrEnum

from pydantic import BaseModel
from typesafe_sdk import Choice

from app.config import Settings
from app.models import Ticket
from app.services.coding_agent import CodingAgent, Iteration
from app.services.jev import Jev


class Action(StrEnum):
    cont = "continue"
    done = "done"
    escalate = "escalate"


NEXT_ACTION = {
    Action.cont: "Making progress; another iteration is likely to help",
    Action.done: "The issue is fixed and the tests confirm it",
    Action.escalate: "Stuck, going in circles, or the fix needs a human decision",
}


class LoopOutcome(BaseModel):
    done: bool
    iterations: int
    reason: str
    last: Iteration | None
    history: list[str]


def decide(action: Action, confidence: float, it: Iteration, same_failures: int, cfg: Settings) -> tuple[Action, str]:
    """Code policy over Jev's answer. Hard stops and tests override Jev."""
    if same_failures >= cfg.same_failure_limit:
        return Action.escalate, f"same test failure {same_failures}x in a row"
    if confidence < cfg.escalate_confidence:
        return Action.escalate, f"Jev confidence {confidence:.2f} < {cfg.escalate_confidence}"
    if action is Action.done and not it.tests_passed:
        # Jev saying done is not enough; tests are the ground truth.
        return Action.cont, "Jev said done but tests fail"
    return action, f"Jev: {action} ({confidence:.2f})"


async def run_loop(ticket: Ticket, spec: str, agent: CodingAgent, jev: Jev, cfg: Settings) -> LoopOutcome:
    history: list[str] = []
    last: Iteration | None = None
    same_failures = 0
    for i in range(1, cfg.max_loop_iterations + 1):
        it = await agent.run_iteration(ticket.issue_number, spec, i)
        if it.tests_passed:
            same_failures = 0
        elif last and not last.tests_passed and it.test_output == last.test_output:
            same_failures += 1
        else:
            same_failures = 1
        last = it
        a = (await jev.ask(
            {"issue": ticket.title, "iteration": i, "max_iterations": cfg.max_loop_iterations,
             "this_iteration": it.model_dump(), "previous": history[-3:]},
            {"next_action": Choice(instructions="What should the coding loop do next?", criteria=NEXT_ACTION)},
        ))["next_action"]
        action, why = decide(Action(a.choice), a.confidence, it, same_failures, cfg)
        history.append(f"#{i}: {it.summary} | tests={'pass' if it.tests_passed else 'fail'} | {why}")
        if action is Action.done:
            return LoopOutcome(done=True, iterations=i, reason=why, last=it, history=history)
        if action is Action.escalate:
            return LoopOutcome(done=False, iterations=i, reason=why, last=it, history=history)
    return LoopOutcome(done=False, iterations=cfg.max_loop_iterations,
                       reason=f"hit max iterations ({cfg.max_loop_iterations})", last=last, history=history)
