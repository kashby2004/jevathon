import { useEffect, useState } from "react";

// Mirrors backend/app/models.py (Ticket, Event). Keep in sync when the model changes.
type Event = { at: string; stage: string; note: string };
type Ticket = {
  issue_number: number;
  title: string;
  stage: string;
  severity: string | null;
  severe: boolean | null;
  topic: string | null;
  triage_confidence: number | null;
  assigned_dev: string | null;
  coder: string | null;
  pr_number: number | null;
  loop_iterations: number;
  check_findings: string[];
  degraded: string[];
  events: Event[];
};
type Health = { modes: Record<string, string>; fake_adapters: string[] };

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const POLL_MS = 3000;

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [h, t] = await Promise.all([get<Health>("/health"), get<Ticket[]>("/tickets")]);
        if (!alive) return;
        setHealth(h);
        setTickets(t);
        setError(null);
      } catch (e) {
        // Surface, don't hide: a stale board that looks live is worse than an error.
        if (alive) setError(`Backend unreachable at ${API} (${(e as Error).message})`);
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <main className="mx-auto max-w-5xl p-4 font-sans text-zinc-900">
      <h1 className="text-2xl font-semibold">On-call routing</h1>
      {error && <p className="mt-3 rounded bg-red-100 p-3 text-red-800">{error}</p>}
      {health && health.fake_adapters.length > 0 && (
        <p className="mt-3 rounded bg-amber-100 p-3 text-amber-900">
          Fake adapters active: {health.fake_adapters.join(", ")}
        </p>
      )}
      <ul className="mt-4 divide-y rounded border">
        {tickets.length === 0 && <li className="p-4 text-zinc-500">No tickets yet.</li>}
        {tickets.map((t) => (
          <li key={t.issue_number} className="p-4">
            <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === t.issue_number ? null : t.issue_number)}>
              <span className="font-mono text-zinc-500">#{t.issue_number}</span>
              <span className="font-medium">{t.title}</span>
              <span className="rounded bg-zinc-200 px-2 text-sm">{t.stage}</span>
              {t.severity && (
                <span className={`rounded px-2 text-sm ${t.severe ? "bg-red-200" : "bg-green-200"}`}>
                  {t.severity}
                  {t.triage_confidence !== null && ` · ${t.triage_confidence.toFixed(2)}`}
                </span>
              )}
              {t.topic && <span className="text-sm text-zinc-500">{t.topic}</span>}
              {t.degraded.length > 0 && <span className="rounded bg-amber-200 px-2 text-sm">degraded</span>}
            </button>
            {open === t.issue_number && (
              <div className="mt-3 space-y-2 text-sm">
                <p>
                  coder: {t.coder ?? "—"} · dev: {t.assigned_dev ?? "—"} · PR: {t.pr_number ?? "—"} · loop iterations: {t.loop_iterations}
                </p>
                {t.check_findings.length > 0 && <p className="text-red-700">findings: {t.check_findings.join("; ")}</p>}
                {t.degraded.map((d) => (
                  <p key={d} className="text-amber-800">⚠ {d}</p>
                ))}
                <ol className="border-l pl-3">
                  {t.events.map((e, i) => (
                    <li key={i}>
                      <span className="font-mono text-zinc-500">{new Date(e.at).toLocaleTimeString()}</span>{" "}
                      <span className="text-zinc-500">[{e.stage}]</span> {e.note}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
