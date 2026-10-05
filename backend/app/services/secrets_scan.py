"""Deterministic secret scan over a diff's added lines. Deliberately not a model: leaked keys must not depend on judgment."""

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import default_settings


def added_lines(diff: str) -> list[str]:
    return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]


def scan_diff(diff: str) -> list[str]:
    """Returns one finding per suspicious added line (secret type only — never echo the value)."""
    findings = []
    with default_settings():
        for i, line in enumerate(added_lines(diff), 1):
            types = sorted({s.type for s in scan_line(line)})
            if types:
                findings.append(f"added line {i}: {', '.join(types)}")
    return findings
