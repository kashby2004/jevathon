"""Inbound dev texts. PROVISIONAL payload shape {"from": "<phone>", "text": "..."} until Photon's
inbound webhook format is confirmed (docs §13 Q4) — adapt the parsing here, nowhere else.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class InboundText(BaseModel):
    from_: str = Field(alias="from")
    text: str


@router.post("/webhooks/photon")
async def photon_webhook(msg: InboundText, request: Request):
    app = request.app.state
    dev = next((d for d in app.orchestrator.devs.values() if d.phone == msg.from_), None)
    if dev is None:
        raise HTTPException(404, f"no dev with phone {msg.from_}")
    t = await app.orchestrator.on_dev_reply(dev.id, msg.text)
    return {"ticket": t.issue_number if t else None, "stage": t.stage if t else None}
