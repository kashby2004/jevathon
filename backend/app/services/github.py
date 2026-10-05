"""GitHub REST: issues are the tickets. Also how CodeRabbit is driven (comments), since it has no API."""

import hashlib
import hmac
from enum import StrEnum
from typing import Protocol

import httpx

API = "https://api.github.com"


class CIStatus(StrEnum):
    success = "success"
    failure = "failure"
    pending = "pending"


class GitHub(Protocol):
    async def comment(self, issue_number: int, body: str) -> None: ...
    async def add_labels(self, issue_number: int, labels: list[str]) -> None: ...
    async def open_pr(self, branch: str, title: str, body: str) -> int: ...
    async def request_reviewer(self, pr_number: int, login: str) -> None: ...
    async def pr_diff(self, pr_number: int) -> str: ...
    async def ci_status(self, pr_number: int) -> CIStatus: ...


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class RealGitHub:
    def __init__(self, token: str, repo: str, base_branch: str) -> None:
        self._repo = repo
        self._base = base_branch
        self._http = httpx.AsyncClient(
            base_url=f"{API}/repos/{repo}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=30,
        )

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        r = await self._http.request(method, path, **kw)
        r.raise_for_status()
        return r

    async def comment(self, issue_number: int, body: str) -> None:
        await self._req("POST", f"/issues/{issue_number}/comments", json={"body": body})

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        await self._req("POST", f"/issues/{issue_number}/labels", json={"labels": labels})

    async def open_pr(self, branch: str, title: str, body: str) -> int:
        r = await self._req("POST", "/pulls", json={"title": title, "head": branch, "base": self._base, "body": body})
        return r.json()["number"]

    async def request_reviewer(self, pr_number: int, login: str) -> None:
        await self._req("POST", f"/pulls/{pr_number}/requested_reviewers", json={"reviewers": [login]})

    async def pr_diff(self, pr_number: int) -> str:
        r = await self._req("GET", f"/pulls/{pr_number}", headers={"Accept": "application/vnd.github.diff"})
        return r.text

    async def ci_status(self, pr_number: int) -> CIStatus:
        sha = (await self._req("GET", f"/pulls/{pr_number}")).json()["head"]["sha"]
        runs = (await self._req("GET", f"/commits/{sha}/check-runs")).json()["check_runs"]
        # No CI configured counts as pending, not success: "no tests ran" must never pass the gate.
        if not runs or any(r["status"] != "completed" for r in runs):
            return CIStatus.pending
        ok = {"success", "neutral", "skipped"}
        return CIStatus.success if all(r["conclusion"] in ok for r in runs) else CIStatus.failure


class FakeGitHub:
    def __init__(self) -> None:
        self.comments: list[tuple[int, str]] = []
        self.labels: dict[int, list[str]] = {}
        self.prs: dict[int, dict] = {}
        self.reviewers: list[tuple[int, str]] = []
        self.diffs: dict[int, str] = {}
        self.ci: dict[int, CIStatus] = {}
        self._next_pr = 1000

    async def comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self.labels.setdefault(issue_number, []).extend(labels)

    async def open_pr(self, branch: str, title: str, body: str) -> int:
        self._next_pr += 1
        self.prs[self._next_pr] = {"branch": branch, "title": title, "body": body}
        return self._next_pr

    async def request_reviewer(self, pr_number: int, login: str) -> None:
        self.reviewers.append((pr_number, login))

    async def pr_diff(self, pr_number: int) -> str:
        return self.diffs.get(pr_number, "")

    async def ci_status(self, pr_number: int) -> CIStatus:
        return self.ci.get(pr_number, CIStatus.success)
