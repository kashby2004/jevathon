"""Texting devs via Photon (iMessage/SMS). Transport only.

Photon's SDK is TypeScript-only (spectrum-ts) — this backend is Python, so it can't speak
Spectrum's API directly. RealPhoton instead calls the small TS sidecar in ../photon-bridge/
(POST /send), which holds the actual Spectrum session. Inbound replies go the other way:
the bridge forwards them to POST /webhooks/photon -> Orchestrator.on_dev_reply.
"""

from typing import Protocol

import httpx
from pydantic import BaseModel


class Message(BaseModel):
    to: str
    text: str


class Photon(Protocol):
    async def send(self, to: str, text: str) -> None: ...


class RealPhoton:
    def __init__(self, bridge_url: str) -> None:
        self._http = httpx.AsyncClient(base_url=bridge_url, timeout=30)

    async def send(self, to: str, text: str) -> None:
        r = await self._http.post("/send", json={"to": to, "text": text})
        r.raise_for_status()


class FakePhoton:
    """Keeps an outbox the dashboard can show (GET /outbox)."""

    def __init__(self) -> None:
        self.outbox: list[Message] = []

    async def send(self, to: str, text: str) -> None:
        self.outbox.append(Message(to=to, text=text))
