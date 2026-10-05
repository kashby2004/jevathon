"""GitHub webhook → orchestrator events. Configure the repo webhook for: Issues, Issue comments,
Pull requests, Check suites. Content type application/json. URL: <tunnel>/webhooks/github.
"""

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import Mode
from app.services.github import verify_signature

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/github")
async def github_webhook(request: Request, x_github_event: str = Header(), x_hub_signature_256: str | None = Header(None)):
    app = request.app.state
    body = await request.body()
    if app.cfg.github_mode is Mode.real and not verify_signature(
        app.cfg.github_webhook_secret.get_secret_value(), body, x_hub_signature_256
    ):
        raise HTTPException(401, "bad signature")
    p = json.loads(body)
    orch = app.orchestrator
    action = p.get("action")

    if x_github_event == "issues" and action == "opened":
        i = p["issue"]
        t = await orch.on_issue_opened(i["number"], i["title"], i.get("body") or "")
        return {"ticket": t.issue_number, "stage": t.stage}

    if x_github_event == "issue_comment" and action == "created":
        # Issue Planner posts on issues, not PRs; PR comments carry a "pull_request" key.
        if p["comment"]["user"]["login"] == app.cfg.coderabbit_login and "pull_request" not in p["issue"]:
            orch.on_coderabbit_plan(p["issue"]["number"], p["comment"]["body"])
        return {"ok": True}

    if x_github_event == "pull_request":
        pr = p["pull_request"]
        if action == "opened":
            await orch.on_pr_opened(pr["number"], pr.get("body") or "")
        elif action == "closed" and pr.get("merged"):
            await orch.on_pr_merged(pr["number"])
        return {"ok": True}

    if x_github_event == "check_suite" and action == "completed":
        for pr in p["check_suite"].get("pull_requests", []):
            await orch.on_ci_completed(pr["number"])
        return {"ok": True}

    return {"ignored": f"{x_github_event}/{action}"}
