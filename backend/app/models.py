from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


class Stage(StrEnum):
    received = "received"
    triaged = "triaged"
    agent_working = "agent_working"
    assigning_dev = "assigning_dev"
    dev_deciding = "dev_deciding"      # dev accepted; choosing "jev" vs "mine"
    dev_working = "dev_working"
    checks = "checks"
    in_review = "in_review"
    merged = "merged"
    needs_human = "needs_human"        # automation stopped; a person must pick it up


class Severity(StrEnum):
    # Order matters: Jev Choice criteria are built from this, most severe first.
    critical = "critical"
    high = "high"
    normal = "normal"
    low = "low"


SEVERE = {Severity.critical, Severity.high}


class Developer(BaseModel):
    id: str
    name: str
    phone: str
    github: str
    skills: list[str]
    on_shift: bool = True
    open_assignments: int = 0
    reviewer: bool = False
    mood: str | None = None  # latest survey result, set via Photon survey


class Event(BaseModel):
    at: datetime = Field(default_factory=now)
    stage: Stage
    note: str


class Ticket(BaseModel):
    issue_number: int
    title: str
    body: str
    stage: Stage = Stage.received
    severity: Severity | None = None
    severe: bool | None = None
    topic: str | None = None
    triage_confidence: float | None = None
    plan: str | None = None                 # CodeRabbit Issue Planner output
    research: str | None = None
    dev_queue: list[str] = []               # ranked dev ids
    asked: list[str] = []                   # dev ids asked so far, in order
    asked_at: datetime | None = None
    assigned_dev: str | None = None
    coder: str | None = None                # "agent" or a dev id
    loop_iterations: int = 0
    pr_number: int | None = None
    check_findings: list[str] = []
    degraded: list[str] = []                # every fallback that fired, surfaced on the dashboard
    events: list[Event] = []

    def log(self, note: str) -> None:
        self.events.append(Event(stage=self.stage, note=note))
