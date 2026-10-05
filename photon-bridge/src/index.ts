import { createServer } from "node:http";
import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";

// Bridges Jevathon's Python backend (no TS-capable Spectrum SDK) to Photon/iMessage.
// Inbound dev replies -> POST backend /webhooks/photon. Outbound texts -> POST /send here.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const PORT = Number(process.env.PORT ?? 4001);
// Spectrum's cloud calls have no timeout of their own; warn if startup stalls this long.
const STARTUP_WARN_MS = Number(process.env.STARTUP_WARN_MS ?? 20_000);

if (!process.env.PROJECT_ID || !process.env.PROJECT_SECRET) {
  console.error("PROJECT_ID / PROJECT_SECRET are not set. Copy .env.example to .env and fill them in.");
  process.exit(1);
}

// Set once Spectrum is connected; until then /send answers 503.
let sendText: ((to: string, text: string) => Promise<void>) | undefined;
let startupStep = "starting";

async function forwardToBackend(from: string, text: string) {
  try {
    const res = await fetch(`${BACKEND_URL}/webhooks/photon`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from, text }),
    });
    if (!res.ok) {
      console.error(`backend rejected inbound reply from ${from}: ${res.status} ${await res.text()}`);
    }
  } catch (err) {
    console.error(`could not reach backend at ${BACKEND_URL} for reply from ${from}:`, err);
  }
}

// The HTTP server starts first so the backend gets a clear 503 (not a refused connection)
// while Spectrum is still connecting, and GET /health shows which startup step it's on.
// Outbound: POST /send {"to": "+1555...", "text": "..."} — matches services/photon.py's RealPhoton.send.
const server = createServer((req, res) => {
  const json = (status: number, body: object) =>
    res.writeHead(status, { "Content-Type": "application/json" }).end(JSON.stringify(body));

  if (req.method === "GET" && req.url === "/health") {
    json(sendText ? 200 : 503, { ready: Boolean(sendText), step: startupStep });
    return;
  }
  if (req.method !== "POST" || req.url !== "/send") {
    res.writeHead(404).end();
    return;
  }
  if (!sendText) {
    json(503, { ok: false, error: `Photon not connected yet (step: ${startupStep})` });
    return;
  }
  const send = sendText;
  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", async () => {
    try {
      const { to, text } = JSON.parse(body);
      await send(to, text);
      json(200, { ok: true });
    } catch (err) {
      console.error("send failed:", err);
      json(502, { ok: false, error: String(err) });
    }
  });
});
server.listen(PORT, () => console.log(`photon-bridge listening on :${PORT} (send) -> backend ${BACKEND_URL} (replies)`));

const watchdog = setTimeout(() => {
  console.error(
    `Spectrum still not connected after ${STARTUP_WARN_MS / 1000}s (step: ${startupStep}). ` +
      "Credentials were accepted if no 401 was printed, so this is waiting on Photon's cloud. " +
      "Check that the project has an iMessage line (`photon spectrum lines list`) and Photon's status.",
  );
}, STARTUP_WARN_MS);

startupStep = "connecting to Spectrum cloud (project lookup + iMessage line tokens)";
console.log(`${startupStep}...`);
const app = await Spectrum({
  projectId: process.env.PROJECT_ID,
  projectSecret: process.env.PROJECT_SECRET,
  providers: [imessage.config()],
});
const im = imessage(app);
sendText = async (to, text) => {
  const space = await im.space.create(await im.user(to));
  await space.send(text);
};
startupStep = "ready";
clearTimeout(watchdog);
console.log("Spectrum connected; ready to send and receive.");

// Inbound: reactive loop, forwards every dev text reply to the backend.
for await (const [, message] of app.messages) {
  if (message.direction === "outbound") continue;
  if (message.content.type !== "text") continue;
  const from = message.sender?.id;
  if (!from) {
    console.error("inbound text with no sender id, dropping:", message.content.text);
    continue;
  }
  console.log(`inbound from ${from}: ${message.content.text}`);
  // A failed reaction must not stop the loop, or the bridge silently stops receiving.
  await message.react("👍").catch((err) => console.error("react failed:", err));
  await forwardToBackend(from, message.content.text);
}
