"""FastAPI entrypoint. Run: `uvicorn app.main:app --reload` from backend/."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Mode, Settings
from app.models import Developer
from app.orchestrator import Orchestrator
from app.services import build_services
from app.services.github import CIStatus, FakeGitHub
from app.services.photon import FakePhoton
from app.store import TicketStore
from app.webhooks import github as github_webhook
from app.webhooks import photon as photon_webhook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def load_roster(cfg: Settings) -> list[Developer]:
    return [Developer(**d) for d in json.loads(cfg.roster_path.read_text())]


async def _sweeper(orch: Orchestrator, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await orch.sweep_timeouts()
        except Exception:
            log.exception("timeout sweep failed")  # keep sweeping; per-ticket crashes are already surfaced


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Settings()
    fakes = cfg.validate_startup()  # raises on real adapters missing keys
    s = app.state
    s.cfg, s.fakes = cfg, fakes
    s.svc = build_services(cfg)
    s.store = TicketStore()
    s.orchestrator = Orchestrator(s.svc, s.store, cfg, load_roster(cfg))
    sweeper = asyncio.create_task(_sweeper(s.orchestrator, cfg.sweep_interval_s))
    yield
    sweeper.cancel()


app = FastAPI(title="jevathon routing", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in Settings().frontend_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(github_webhook.router)
app.include_router(photon_webhook.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)})


@app.get("/health")
async def health(request: Request):
    s = request.app.state
    return {"ok": True, "modes": s.cfg.modes(), "fake_adapters": s.fakes}


@app.get("/tickets")
async def tickets(request: Request):
    return sorted(request.app.state.store.all(), key=lambda t: t.issue_number, reverse=True)


@app.get("/tickets/{number}")
async def ticket(number: int, request: Request):
    t = request.app.state.store.get(number)
    if t is None:
        raise HTTPException(404)
    return t


@app.get("/devs")
async def devs(request: Request):
    return list(request.app.state.orchestrator.devs.values())


@app.get("/outbox")
async def outbox(request: Request):
    photon = request.app.state.svc.photon
    if not isinstance(photon, FakePhoton):
        raise HTTPException(404, "outbox only exists with PHOTON_MODE=fake")
    return photon.outbox


# ---------- demo simulation (fake GitHub only: these would bypass webhook auth otherwise) ----------

class SimIssue(BaseModel):
    number: int
    title: str
    body: str = ""


class SimReply(BaseModel):
    dev_id: str
    text: str


class SimPR(BaseModel):
    pr_number: int
    issue_number: int
    diff: str = ""
    ci: CIStatus = CIStatus.success


def _fake_github(request: Request) -> FakeGitHub:
    if request.app.state.cfg.github_mode is not Mode.fake:
        raise HTTPException(403, "simulation endpoints need GITHUB_MODE=fake")
    return request.app.state.svc.github


@app.post("/simulate/issue")
async def sim_issue(body: SimIssue, request: Request):
    _fake_github(request)
    return await request.app.state.orchestrator.on_issue_opened(body.number, body.title, body.body)


@app.post("/simulate/reply")
async def sim_reply(body: SimReply, request: Request):
    return await request.app.state.orchestrator.on_dev_reply(body.dev_id, body.text)


@app.post("/simulate/pr")
async def sim_pr(body: SimPR, request: Request):
    gh = _fake_github(request)
    gh.diffs[body.pr_number] = body.diff
    gh.ci[body.pr_number] = body.ci
    return await request.app.state.orchestrator.on_pr_opened(body.pr_number, f"Fixes #{body.issue_number}")


@app.post("/simulate/merge/{pr_number}")
async def sim_merge(pr_number: int, request: Request):
    _fake_github(request)
    await request.app.state.orchestrator.on_pr_merged(pr_number)
    return request.app.state.store.by_pr(pr_number)
