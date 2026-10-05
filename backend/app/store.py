"""In-memory ticket store. State is lost on restart — acceptable for the local demo (docs §10 out of scope)."""

from app.models import Stage, Ticket


class TicketStore:
    def __init__(self) -> None:
        self._tickets: dict[int, Ticket] = {}

    def put(self, ticket: Ticket) -> None:
        self._tickets[ticket.issue_number] = ticket

    def get(self, issue_number: int) -> Ticket | None:
        return self._tickets.get(issue_number)

    def all(self) -> list[Ticket]:
        return list(self._tickets.values())

    def by_pr(self, pr_number: int) -> Ticket | None:
        return next((t for t in self._tickets.values() if t.pr_number == pr_number), None)

    def ticket_for_reply(self, dev_id: str) -> Ticket | None:
        """The ticket a dev's text refers to. Texts carry no ticket id, so use the most recent open conversation."""

        def involves(t: Ticket) -> bool:
            if t.stage is Stage.assigning_dev:
                return bool(t.asked) and t.asked[-1] == dev_id
            return t.stage in (Stage.dev_deciding, Stage.dev_working) and t.assigned_dev == dev_id

        pending = [t for t in self._tickets.values() if involves(t)]
        return max(pending, key=lambda t: t.events[-1].at if t.events else t.asked_at, default=None)
