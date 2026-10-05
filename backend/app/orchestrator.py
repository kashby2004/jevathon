"""The state machine. The only module that changes Ticket.stage. Event handlers are called by webhooks
(GitHub, Photon) and the timeout sweeper; long work (Ralph loop, research) runs as background tasks.
"""

import asyncio
import logging
import re
from collections.abc import Coroutine
from contextlib import contextmanager
from datetime import datetime

from app.config import Settings
from app.models import Developer, Stage, Ticket, now
from app.services import Services
from app.stages import dev_routing
from app.stages.checks import Status, run_checks
from app.stages.handoff import hand_off
from app.stages.ralph_loop import run_loop
from app.stages.replies import ASSIGNMENT, WHO_CODES, Reply, interpret
from app.stages.research import research
from app.stages.triage import triage
from app.store import TicketStore

log = logging.getLogger(__name__)

LINKED_ISSUE = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
AGENT = "agent"


class Orchestrator:
    def __init__(self, svc: Services, store: TicketStore, cfg: Settings, devs: list[Developer]) -> None:
        self.svc, self.store, self.cfg = svc, store, cfg
        self.devs = {d.id: d for d in devs}
        self._plan_ready: dict[int, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---------- plumbing ----------

    def _move(self, t: Ticket, stage: Stage, note: str) -> None:
        t.stage = stage
        t.log(note)

    def _degrade(self, t: Ticket, msg: str) -> None:
        log.error("ticket #%s degraded: %s", t.issue_number, msg)
        t.degraded.append(msg)
        t.log(f"DEGRADED: {msg}")

    @contextmanager
    def _guard(self, t: Ticket):
        """Any crash parks the ticket in needs_human and is logged — never a silently stuck ticket."""
        try:
            yield
        except Exception as e:
            log.exception("ticket #%s crashed", t.issue_number)
            self._degrade(t, f"crash: {type(e).__name__}: {e}")
            self._move(t, Stage.needs_human, "automation crashed; needs a person")
            raise

    def _spawn(self, t: Ticket, coro: Coroutine) -> None:
        async def run():
            with self._guard(t):
                await coro

        task = asyncio.create_task(run())
        self._tasks.add(task)
        # Exception already logged + surfaced by _guard; retrieve it so asyncio doesn't warn on GC.
        task.add_done_callback(lambda tk: (self._tasks.discard(tk), tk.cancelled() or tk.exception()))

    async def drain(self) -> None:
        """Wait for background work (tests / shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---------- intake + triage ----------

    async def on_issue_opened(self, number: int, title: str, body: str) -> Ticket:
        if existing := self.store.get(number):
            return existing  # webhook redelivery
        t = Ticket(issue_number=number, title=title, body=body)
        self.store.put(t)
        t.log("issue received")
        self._plan_ready[number] = asyncio.Event()
        with self._guard(t):
            await self.svc.github.comment(number, "@coderabbitai plan")
            r = await triage(t, self.svc.jev, self.cfg)
            t.severity, t.severe, t.topic, t.triage_confidence = r.severity, r.severe, r.topic, r.confidence
            if r.blocked:
                self._move(t, Stage.needs_human, r.reason)
                await self.svc.github.comment(number, f"Automation stopped: {r.reason}. A human needs to triage this.")
                return t
            self._move(t, Stage.triaged, r.reason)
            if r.severe:
                await self._start_assignment(t, "severe issue")
            else:
                self._move(t, Stage.agent_working, "non-severe → Ralph loop")
                self._spawn(t, self._agent_path(t))
        return t

    def on_coderabbit_plan(self, number: int, plan: str) -> None:
        t = self.store.get(number)
        if t is None:
            log.warning("CodeRabbit plan for unknown issue #%s ignored", number)
            return
        t.plan = plan
        t.log("CodeRabbit plan received")
        if ev := self._plan_ready.get(number):
            ev.set()

    async def _wait_for_plan(self, t: Ticket) -> None:
        if t.plan:
            return
        ev = self._plan_ready.setdefault(t.issue_number, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), self.cfg.coderabbit_plan_wait_s)
        except TimeoutError:
            self._degrade(t, f"no CodeRabbit plan within {self.cfg.coderabbit_plan_wait_s}s; proceeding without it")

    # ---------- agent path ----------

    async def _agent_path(self, t: Ticket) -> None:
        await self._wait_for_plan(t)
        t.coder = AGENT
        spec = f"Issue #{t.issue_number}: {t.title}\n\n{t.body}\n\nPlan:\n{t.plan or '(none)'}"
        if t.check_findings:
            spec += "\n\nPrevious attempt failed checks:\n" + "\n".join(t.check_findings)
        out = await run_loop(t, spec, self.svc.agent, self.svc.jev, self.cfg)
        t.loop_iterations += out.iterations
        for h in out.history:
            t.log(f"loop {h}")
        if not out.done:
            await self._escalate(t, f"agent loop stopped: {out.reason}")
            return
        pr = await self.svc.github.open_pr(
            out.last.branch, f"Fix #{t.issue_number}: {t.title}",
            f"Fixes #{t.issue_number}\n\nAuthored by the Jev-routed agent loop.\n\n" + "\n".join(out.history),
        )
        t.pr_number = pr
        await self._run_checks(t)

    async def _escalate(self, t: Ticket, reason: str) -> None:
        """Agent couldn't finish. A supervising dev gets it back; otherwise route to a dev from scratch."""
        if t.assigned_dev:
            dev = self.devs[t.assigned_dev]
            self._move(t, Stage.dev_deciding, f"escalated to supervising dev {dev.name}: {reason}")
            await self.svc.photon.send(dev.phone, f"Issue #{t.issue_number}: the agent stopped ({reason}). "
                                                  "Reply 'jev' to let it retry or 'mine' to take over.")
        else:
            await self._start_assignment(t, reason)

    # ---------- dev path ----------

    async def _start_assignment(self, t: Ticket, reason: str) -> None:
        self._move(t, Stage.assigning_dev, reason)
        pool = dev_routing.eligible(list(self.devs.values()), self.cfg)
        if not pool:
            self._degrade(t, "no eligible devs (all off shift or at max load)")
            self._move(t, Stage.needs_human, "no one to assign")  # docs §13 Q3: page on-call lead?
            return
        t.dev_queue = await dev_routing.rank(t, pool, self.svc.jev)
        t.asked = []
        t.log("dev ranking: " + ", ".join(self.devs[d].name for d in t.dev_queue))
        self._spawn(t, self._research(t))
        await self._ask_next(t)

    async def _research(self, t: Ticket) -> None:
        await self._wait_for_plan(t)
        t.research = await research(t, self.svc.llm, self.svc.browserbase, self.svc.jev, self.cfg)
        t.log("research ready")
        # Dev may already have accepted before research finished — send it late rather than never.
        if t.assigned_dev and t.stage in (Stage.dev_deciding, Stage.dev_working):
            await self.svc.photon.send(self.devs[t.assigned_dev].phone, f"Research for #{t.issue_number}:\n{t.research}")

    async def _ask_next(self, t: Ticket) -> None:
        nxt = dev_routing.next_ask(t, self.cfg)
        dev = self.devs[nxt.dev_id]
        if nxt.mandatory:
            t.log(f"all {len(t.asked)} asked devs declined → mandatory assignment")
            await self._assign(t, dev, mandatory=True)
            return
        t.asked.append(dev.id)
        t.asked_at = now()
        t.log(f"asked {dev.name} ({len(t.asked)}/{min(self.cfg.dev_cascade_size, len(t.dev_queue))})")
        await self.svc.photon.send(dev.phone, f"[{t.severity}] Issue #{t.issue_number}: {t.title} (topic: {t.topic}). "
                                              "Can you take it? Reply yes or no.")

    async def _assign(self, t: Ticket, dev: Developer, mandatory: bool) -> None:
        t.assigned_dev = dev.id
        t.asked_at = None
        dev.open_assignments += 1
        self._move(t, Stage.dev_deciding, f"assigned to {dev.name}" + (" (mandatory)" if mandatory else ""))
        opener = "You've been assigned (all other candidates declined)." if mandatory else "Thanks for taking it."
        parts = [f"{opener} Issue #{t.issue_number}: {t.title}"]
        if t.plan:
            parts.append(f"CodeRabbit plan:\n{t.plan}")
        if t.research:
            parts.append(f"Research:\n{t.research}")
        loop_notes = [e.note for e in t.events if e.note.startswith("loop ")]
        if loop_notes:
            parts.append("Agent attempts so far:\n" + "\n".join(loop_notes))
        parts.append("Reply 'jev' for the agent to code it (you supervise) or 'mine' to code it yourself.")
        await self.svc.photon.send(dev.phone, "\n\n".join(parts))

    async def on_dev_reply(self, dev_id: str, text: str) -> Ticket | None:
        t = self.store.ticket_for_reply(dev_id)
        if t is None:
            log.warning("reply from %s matches no open ticket: %r", dev_id, text)
            return None
        dev = self.devs[dev_id]
        t.log(f"reply from {dev.name}: {text!r}")
        with self._guard(t):
            if t.stage is Stage.assigning_dev:
                r = await interpret(text, ASSIGNMENT, self.svc.jev, self.cfg)
                if r is Reply.accept:
                    await self._assign(t, dev, mandatory=False)
                elif r is Reply.decline:
                    await self._ask_next(t)
                else:
                    await self.svc.photon.send(dev.phone, f"Sorry, didn't catch that — can you take issue #{t.issue_number}? yes/no")
            elif t.stage is Stage.dev_deciding:
                r = await interpret(text, WHO_CODES, self.svc.jev, self.cfg)
                if r is Reply.agent:
                    self._move(t, Stage.agent_working, f"{dev.name} chose the agent; supervising")
                    self._spawn(t, self._agent_path(t))
                elif r is Reply.self_:
                    t.coder = dev.id
                    self._move(t, Stage.dev_working, f"{dev.name} is coding it")
                    await self.svc.photon.send(dev.phone, f"Great — open a PR with 'Fixes #{t.issue_number}' in the description when ready.")
                else:
                    await self.svc.photon.send(dev.phone, "Reply 'jev' for the agent to code it, or 'mine' to code it yourself.")
            # dev_working: status update, already logged above
        return t

    async def sweep_timeouts(self, at: datetime | None = None) -> None:
        at = at or now()
        for t in self.store.all():
            if t.stage is Stage.assigning_dev and dev_routing.reply_overdue(t, at, self.cfg):
                t.log(f"{self.devs[t.asked[-1]].name} didn't reply in time → treated as decline")
                with self._guard(t):
                    await self._ask_next(t)

    # ---------- PR / checks / review ----------

    async def on_pr_opened(self, pr_number: int, body: str) -> Ticket | None:
        m = LINKED_ISSUE.search(body or "")
        t = self.store.get(int(m.group(1))) if m else None
        if t is None or t.pr_number == pr_number:
            return t  # unrelated PR, or the agent's own PR already being checked
        t.pr_number = pr_number
        with self._guard(t):
            await self._run_checks(t)
        return t

    async def on_ci_completed(self, pr_number: int) -> None:
        t = self.store.by_pr(pr_number)
        if t and t.stage in (Stage.checks, Stage.dev_working):
            with self._guard(t):
                await self._run_checks(t)

    async def _run_checks(self, t: Ticket) -> None:
        self._move(t, Stage.checks, f"checking PR #{t.pr_number}")
        res = await run_checks(t.pr_number, t.title, self.svc.github, self.svc.jev, self.cfg)
        t.check_findings = res.findings
        if res.status is Status.pending:
            t.log("waiting for CI")
        elif res.status is Status.failed:
            t.log("checks failed: " + "; ".join(res.findings))
            if t.coder == AGENT:
                await self._escalate(t, "checks failed: " + "; ".join(res.findings))
            else:
                self._move(t, Stage.dev_working, "back to dev with findings")
                await self.svc.photon.send(self.devs[t.coder].phone, f"PR #{t.pr_number} failed checks:\n" + "\n".join(res.findings))
        else:
            reviewer = await hand_off(t, list(self.devs.values()), self.svc.github, self.svc.photon)
            if reviewer is None:
                self._degrade(t, "no reviewer available; PR needs a reviewer assigned by hand")
            self._move(t, Stage.in_review, f"reviewer: {reviewer.name if reviewer else 'none'}")

    async def on_pr_merged(self, pr_number: int) -> None:
        t = self.store.by_pr(pr_number)
        if t is None:
            return
        if t.assigned_dev:
            self.devs[t.assigned_dev].open_assignments -= 1
        self._move(t, Stage.merged, f"PR #{pr_number} merged")
