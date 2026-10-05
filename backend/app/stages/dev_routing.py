"""Step 3b: filter devs in code, rank with Jev, then the ask/decline cascade (docs §5)."""

from datetime import datetime

from typesafe_sdk import Choice

from app.config import Settings
from app.models import Developer, Ticket
from app.services.jev import Jev


def eligible(devs: list[Developer], cfg: Settings) -> list[Developer]:
    # Availability and load are filtered here, not by Jev: it is unreliable at time comparison and counting.
    return [d for d in devs if d.on_shift and d.open_assignments < cfg.max_open_assignments]


async def rank(ticket: Ticket, devs: list[Developer], jev: Jev) -> list[str]:
    """Dev ids best-first, ordered by Jev's probability for each."""
    if len(devs) <= 1:
        return [d.id for d in devs]
    criteria = {d.id: {"skills": d.skills, "mood": d.mood or "unknown"} for d in devs}
    a = await jev.ask(
        {"issue": {"title": ticket.title, "body": ticket.body}, "topic": ticket.topic},
        {"best_dev": Choice(instructions="Which developer is the best fit to fix this issue right now, "
                                         "given their skills and how they are feeling?", criteria=criteria)},
    )
    probs = a["best_dev"].probabilities
    return sorted(probs, key=probs.get, reverse=True)


class NextAsk:
    """What the cascade does next: ask a dev, or assign one without asking."""

    def __init__(self, dev_id: str, mandatory: bool) -> None:
        self.dev_id = dev_id
        self.mandatory = mandatory


def next_ask(ticket: Ticket, cfg: Settings) -> NextAsk:
    """#1 → #2 → #3, then #1 is assigned mandatorily. Shorter queue → shorter cascade."""
    limit = min(cfg.dev_cascade_size, len(ticket.dev_queue))
    if len(ticket.asked) < limit:
        return NextAsk(ticket.dev_queue[len(ticket.asked)], mandatory=False)
    return NextAsk(ticket.dev_queue[0], mandatory=True)


def reply_overdue(ticket: Ticket, now: datetime, cfg: Settings) -> bool:
    """No reply within the timeout counts as a decline, so one silent dev can't stall a ticket."""
    if ticket.asked_at is None:
        return False
    return (now - ticket.asked_at).total_seconds() > cfg.reply_timeout_s(bool(ticket.severe))
