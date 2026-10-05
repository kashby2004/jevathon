"""Step 7: tests (CI) + secrets (deterministic) + AI slop (Jev), on both the agent and dev paths."""

import re
from enum import StrEnum

from pydantic import BaseModel
from typesafe_sdk import Choice, Noul, Score

from app.config import Settings
from app.services.github import CIStatus, GitHub
from app.services.jev import Jev
from app.services.secrets_scan import scan_diff

# Final AI-slop rubric (source: AISlopqs.txt). Keys become finding labels.
# Each spec's "type" says which typesafe_sdk Question to build (see _build_question):
# noul (yes/no probability), choice (pick one criterion), or score (severity level).
SLOP_QUESTIONS: dict[str, dict] = {
    "requirement_coverage": {
        "type": "choice",
        "instructions": "How well does `diff` implement `requirement`?",
        "criteria": {
            "complete": "The required behavior is present.",
            "partial": "Some required behavior is missing.",
            "unrelated": "The change does not address the requirement.",
            "unclear": "The supplied evidence is insufficient to decide.",
        },
    },
    "invented_interface": {
        "type": "noul",
        "instructions": (
            "Does `diff` call an API, method, or data field that conflicts "
            "with `contracts` or `repo_context`?"
        ),
    },
    "duplicate_implementation": {
        "type": "noul",
        "instructions": (
            "Does `diff` reimplement functionality already shown in "
            "`repo_context`?"
        ),
    },
    "abstraction_fit": {
        "type": "choice",
        "instructions": "How does the main new abstraction in `diff` fit `requirement`?",
        "criteria": {
            "necessary": "It directly supports a concrete requirement.",
            "premature": "It prepares for possible future needs without a current use.",
            "redundant": "An existing repository abstraction already serves this purpose.",
            "none_added": "No meaningful abstraction was added.",
            "unclear": "The supplied evidence is insufficient to decide.",
        },
    },
    "unnecessary_dependency": {
        "type": "noul",
        "instructions": (
            "Does `diff` introduce a dependency for behavior already available "
            "through the supplied repository code or existing dependencies?"
        ),
    },
    "failure_handling": {
        "type": "choice",
        "instructions": "How does `diff` handle failures in the changed behavior?",
        "criteria": {
            "handled": "Failures are handled or propagated as required.",
            "swallowed": "An error can be silently suppressed.",
            "false_success": "A failed operation can be reported as successful.",
            "not_applicable": "The change has no relevant failure path.",
            "unclear": "The supplied evidence is insufficient to decide.",
        },
    },
    "test_quality": {
        "type": "choice",
        "instructions": "What is the main limitation of `tests` for the behavior in `diff`?",
        "criteria": {
            "meaningful": "Tests check the required observable behavior.",
            "happy_path_only": "Tests omit a material failure or edge case.",
            "mirrors_implementation": "Tests repeat implementation assumptions without verifying the requirement.",
            "missing": "No relevant test is supplied.",
            "unclear": "The supplied evidence is insufficient to decide.",
        },
    },
    "misleading_comments": {
        "type": "noul",
        "instructions": (
            "Does an added comment or docstring in `diff` claim behavior "
            "that the changed code does not perform?"
        ),
    },
    "primary_concern": {
        "type": "choice",
        "instructions": "Which concern deserves the first human review?",
        "criteria": {
            "wrong_behavior": "The change appears to produce an incorrect result.",
            "contract_mismatch": "The change conflicts with a supplied interface or schema.",
            "hidden_failure": "The change conceals or misreports a failure.",
            "duplication": "The change repeats existing functionality.",
            "excess_complexity": "The change adds structure without a current need.",
            "test_gap": "The main concern is missing or weak tests.",
            "none_evident": "No concern is supported by the supplied evidence.",
            "insufficient_context": "The evidence does not support a classification.",
        },
    },
    "impact": {
        "type": "score",
        "instructions": "What is the impact of the strongest concern supported by the supplied evidence?",
        "criteria": [
            "No concern evident",
            "Minor maintenance cost",
            "Behavior may be wrong in an edge case",
            "Required behavior can fail",
            "Critical path can fail or report a false success",
        ],
    },
}

# noul questions flag on cfg.slop_noul_threshold; these choice questions flag on
# specific criteria values instead — DESIGN CALL, not in AISlopqs.txt, revisit if wrong.
FLAGGED_CHOICES: dict[str, set[str]] = {
    "requirement_coverage": {"partial", "unrelated", "unclear"},
    "abstraction_fit": {"premature", "redundant", "unclear"},
    "failure_handling": {"swallowed", "false_success", "unclear"},
    "test_quality": {"happy_path_only", "mirrors_implementation", "missing", "unclear"},
}

# primary_concern/impact are a summary pair, not standalone findings: only surface
# them when a real concern was chosen and its impact clears cfg.slop_impact_floor.
_NO_CONCERN = {"none_evident", "insufficient_context"}


def _build_question(spec: dict) -> Choice | Noul | Score:
    if spec["type"] == "noul":
        return Noul(instructions=spec["instructions"])
    if spec["type"] == "choice":
        return Choice(instructions=spec["instructions"], criteria=spec["criteria"])
    if spec["type"] == "score":
        return Score(instructions=spec["instructions"], criteria=spec["criteria"])
    raise ValueError(f"unknown slop question type: {spec['type']}")

_FILE_SPLIT = re.compile(r"^diff --git a/(\S+) b/\S+$", re.MULTILINE)


class Status(StrEnum):
    passed = "passed"
    failed = "failed"
    pending = "pending"   # CI still running; re-run on the check_suite webhook


class ChecksResult(BaseModel):
    status: Status
    findings: list[str]


def split_diff(diff: str) -> dict[str, str]:
    """Per-file hunks. Jev gets one file at a time: large irrelevant state lowers its accuracy."""
    parts = _FILE_SPLIT.split(diff)
    # split → [preamble, path1, body1, path2, body2, ...]
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


async def run_checks(pr_number: int, issue_title: str, github: GitHub, jev: Jev, cfg: Settings) -> ChecksResult:
    findings: list[str] = []

    ci = await github.ci_status(pr_number)
    if ci is CIStatus.failure:
        findings.append("tests: CI failed")

    diff = await github.pr_diff(pr_number)
    findings += [f"secrets: {f}" for f in scan_diff(diff)]

    for path, hunk in split_diff(diff).items():
        state = {"issue": issue_title, "file": path, "diff": hunk}
        a = await jev.ask(state, {k: _build_question(spec) for k, spec in SLOP_QUESTIONS.items()})

        for k, spec in SLOP_QUESTIONS.items():
            if spec["type"] == "noul" and a[k].noul > cfg.slop_noul_threshold:
                findings.append(f"slop: {path}: {k} (p={a[k].noul:.2f})")
            elif k in FLAGGED_CHOICES and a[k].choice in FLAGGED_CHOICES[k]:
                findings.append(f"slop: {path}: {k}={a[k].choice} (conf={a[k].confidence:.2f})")

        concern, impact = a["primary_concern"].choice, a["impact"].score
        if concern not in _NO_CONCERN and impact > cfg.slop_impact_floor:
            findings.append(f"slop: {path}: primary_concern={concern} (impact={impact:.2f})")

    if findings:
        return ChecksResult(status=Status.failed, findings=findings)
    if ci is CIStatus.pending:
        return ChecksResult(status=Status.pending, findings=["tests: CI pending or not configured"])
    return ChecksResult(status=Status.passed, findings=[])
