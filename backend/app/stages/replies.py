"""Interpreting free-text dev replies with Jev instead of keyword matching ("sure, on it" ≠ "yes")."""

from enum import StrEnum

from typesafe_sdk import Choice

from app.config import Settings
from app.services.jev import Jev


class Reply(StrEnum):
    accept = "accept"
    decline = "decline"
    agent = "agent"    # "let Jev do it"
    self_ = "self"     # "I'll code it"
    unclear = "unclear"


ASSIGNMENT = {
    Reply.accept: "Agrees to take the issue",
    Reply.decline: "Declines, is busy, or is unavailable",
    Reply.unclear: "Neither clearly yes nor no",
}
WHO_CODES = {
    Reply.agent: "Wants the automated agent (Jev) to do the coding",
    Reply.self_: "Will write the fix themselves",
    Reply.unclear: "Doesn't say who will code it",
}


async def interpret(text: str, options: dict[str, str], jev: Jev, cfg: Settings) -> Reply:
    a = (await jev.ask({"reply": text}, {"intent": Choice(
        instructions="What does the developer mean by this text reply?", criteria=options)}))["intent"]
    if a.confidence < cfg.reply_parse_confidence_floor:
        return Reply.unclear
    return Reply(a.choice)
