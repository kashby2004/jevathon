"""The coding agent the Ralph loop drives. One call = one iteration with fresh context.

RealCodingAgent generates fixes with a GMI model in a git worktree of the target repo (one branch per
issue). Each iteration: GMI picks the files it needs → GMI returns full new contents for the files it
changes → the edits are applied, the repo's tests run, and the result is committed. Progress persists in
the branch, not in the model's context; only the previous iteration's test output is carried over.

SECURITY: tests run model-written code directly on this machine. The design calls for a per-ticket
Docker sandbox (docs §6); until that exists, only point AGENT_REPO_DIR at a repo you're willing to run
untrusted code from.
"""

import asyncio
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.services.llm import GmiLLM

log = logging.getLogger(__name__)


class Iteration(BaseModel):
    summary: str
    diff_stat: str
    tests_passed: bool
    test_output: str
    branch: str


class CodingAgent(Protocol):
    async def run_iteration(self, issue_number: int, spec: str, iteration: int) -> Iteration: ...


class AgentSettings(BaseModel):
    repo_dir: Path
    work_dir: Path
    base_branch: str
    test_cmd: str
    test_timeout_s: int
    max_context_files: int
    file_chars: int
    output_chars: int
    push: bool


PICK_FILES_SYSTEM = (
    "You are a senior engineer fixing a bug. Given an issue and the repository's file list, choose the "
    "files you need to read to fix it. Reply with only JSON: {\"files\": [\"path\", ...]}"
)
EDIT_SYSTEM = (
    "You are a senior engineer fixing a bug with the smallest correct change. Follow the existing code "
    "style. Add or update a test that proves the fix when the repo has tests. Do not add dependencies, "
    "speculative abstractions, or comments that describe what the code doesn't do. Reply with only JSON: "
    "{\"summary\": \"one line\", \"edits\": [{\"path\": \"relative/path\", \"content\": \"full new file content\"}]}"
)
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json(text: str) -> dict:
    """Models often wrap JSON in prose or code fences; take the outermost object."""
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ValueError(f"model reply had no JSON object: {text[:200]!r}")
    return json.loads(m.group(0))


async def _run(cmd: list[str], cwd: Path, timeout: int | None = None) -> tuple[int, str]:
    # No bytecode cache: an edit made within the same second as the last run can otherwise reuse stale .pyc.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {timeout}s: {' '.join(cmd)}"
    return proc.returncode, out.decode(errors="replace")


async def _git(cwd: Path, *args: str) -> str:
    code, out = await _run(["git", *args], cwd)
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.strip()}")
    return out


class RealCodingAgent:
    def __init__(self, api_key: str, base_url: str, model: str, settings: AgentSettings) -> None:
        self._llm = GmiLLM(api_key, base_url, model)
        self._s = settings
        self._last_output: dict[int, str] = {}  # previous iteration's test output, per issue

    def _branch(self, issue_number: int) -> str:
        return f"jev/issue-{issue_number}"

    async def _worktree(self, issue_number: int) -> Path:
        path = self._s.work_dir / f"issue-{issue_number}"
        if not path.exists():
            self._s.work_dir.mkdir(parents=True, exist_ok=True)
            await _git(self._s.repo_dir, "fetch", "origin", self._s.base_branch)
            await _git(self._s.repo_dir, "worktree", "add", "-B", self._branch(issue_number),
                       str(path), f"origin/{self._s.base_branch}")
        return path

    def _resolve(self, root: Path, rel: str) -> Path:
        """Model-supplied paths must stay inside the worktree and out of .git."""
        path = (root / rel).resolve()
        if not path.is_relative_to(root.resolve()) or ".git" in path.relative_to(root.resolve()).parts:
            raise ValueError(f"model tried to touch a path outside the repo: {rel!r}")
        return path

    async def _pick_files(self, root: Path, spec: str, files: list[str]) -> list[str]:
        reply = await self._llm.complete(PICK_FILES_SYSTEM, f"{spec}\n\nRepository files:\n" + "\n".join(files))
        wanted = [f for f in parse_json(reply).get("files", []) if f in set(files)]
        return wanted[: self._s.max_context_files]

    async def run_iteration(self, issue_number: int, spec: str, iteration: int) -> Iteration:
        root = await self._worktree(issue_number)
        files = (await _git(root, "ls-files")).splitlines()

        context = ""
        for rel in await self._pick_files(root, spec, files):
            text = self._resolve(root, rel).read_text(errors="replace")[: self._s.file_chars]
            context += f"\n--- {rel} ---\n{text}\n"
        prompt = f"{spec}\n\nIteration {iteration}.\n\nFiles:\n{context or '(none selected)'}"
        if last := self._last_output.get(issue_number):
            prompt += f"\n\nThe previous attempt's tests failed with:\n{last}"

        result = parse_json(await self._llm.complete(EDIT_SYSTEM, prompt))
        edited = []
        for edit in result.get("edits", []):
            path = self._resolve(root, edit["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(edit["content"])
            edited.append(str(path.relative_to(root.resolve())))

        code, output = await _run(shlex.split(self._s.test_cmd), root, self._s.test_timeout_s)
        output = output[-self._s.output_chars:]
        passed = code == 0
        self._last_output[issue_number] = "" if passed else output

        summary = result.get("summary") or f"iteration {iteration}"
        # Stage only the model's edits: running tests leaves caches (__pycache__, .pytest_cache) behind.
        if edited:
            await _git(root, "add", "--", *edited)
        if (await _git(root, "diff", "--cached", "--name-only")).strip():
            await _git(root, "commit", "-m", f"Jev agent #{issue_number}, iteration {iteration}: {summary}")
        if self._s.push:
            await _git(root, "push", "--force-with-lease", "-u", "origin", self._branch(issue_number))

        diff_stat = (await _git(root, "diff", "--shortstat", f"origin/{self._s.base_branch}...HEAD")).strip()
        log.info("agent #%s iteration %s: tests %s, %s", issue_number, iteration,
                 "passed" if passed else "failed", diff_stat or "no changes")
        return Iteration(summary=summary, diff_stat=diff_stat or "no changes", tests_passed=passed,
                         test_output=output, branch=self._branch(issue_number))


class FakeCodingAgent:
    """Tests pass from iteration `passes_at` onward (1-based); never passes if None."""

    def __init__(self, passes_at: int | None = 2) -> None:
        self.passes_at = passes_at

    async def run_iteration(self, issue_number: int, spec: str, iteration: int) -> Iteration:
        passed = self.passes_at is not None and iteration >= self.passes_at
        return Iteration(
            summary=f"[fake-agent] iteration {iteration}",
            diff_stat="1 file changed",
            tests_passed=passed,
            test_output="ok" if passed else "FAILED test_fix",
            branch=f"jev/issue-{issue_number}",
        )
