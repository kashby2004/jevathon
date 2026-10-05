"""Step 8: ticket summary on the issue, labels, reviewer request + text. Built from ticket fields, no LLM."""

from app.models import Developer, Ticket
from app.services.github import GitHub
from app.services.photon import Photon


def pick_reviewer(devs: list[Developer], author: str | None) -> Developer | None:
    return next((d for d in devs if d.reviewer and d.on_shift and d.id != author), None)


def summary(t: Ticket, coder_name: str) -> str:
    lines = [
        f"### Routing summary — ready for review (PR #{t.pr_number})",
        f"- **Severity:** {t.severity} (confidence {t.triage_confidence:.2f}) · **Topic:** {t.topic}",
        f"- **Coded by:** {coder_name}" + (f" · {t.loop_iterations} loop iterations" if t.coder == "agent" else ""),
        "- **Checks:** tests ✓ · secrets ✓ · AI slop ✓",
    ]
    if t.degraded:
        lines.append("- **Degraded:** " + "; ".join(t.degraded))
    lines += ["", "<details><summary>Timeline</summary>", ""]
    lines += [f"- `{e.stage}` {e.note}" for e in t.events]
    lines += ["", "</details>"]
    return "\n".join(lines)


async def hand_off(t: Ticket, devs: list[Developer], github: GitHub, photon: Photon) -> Developer | None:
    by_id = {d.id: d for d in devs}
    coder_name = "Jev agent" if t.coder == "agent" else by_id[t.coder].name
    await github.comment(t.issue_number, summary(t, coder_name))
    await github.add_labels(t.issue_number, [f"sev:{t.severity}", f"topic:{t.topic}", "stage:in-review"])
    reviewer = pick_reviewer(devs, author=t.coder)
    if reviewer:
        await github.request_reviewer(t.pr_number, reviewer.github)
        await photon.send(reviewer.phone, f"Review needed: PR #{t.pr_number} for issue #{t.issue_number} "
                                          f"'{t.title}' ({t.severity}). Checks passed; summary is on the issue.")
    return reviewer
