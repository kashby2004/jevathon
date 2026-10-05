"""RealCodingAgent against a throwaway git repo, with GMI replaced by scripted replies. No network."""

import asyncio
import json
import subprocess
import sys

import pytest

from app.services.coding_agent import AgentSettings, RealCodingAgent, parse_json

BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


class ScriptedLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A bare 'origin' plus a clone with a buggy add() and a failing test."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(clone))
    (clone / "calc.py").write_text(BUGGY)
    (clone / "test_calc.py").write_text(TEST)
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    git(clone, "push", "origin", "main")
    return clone


def make_agent(repo, tmp_path, llm):
    agent = RealCodingAgent("key", "https://example.invalid", "model", AgentSettings(
        repo_dir=repo, work_dir=tmp_path / "work", base_branch="main",
        test_cmd=f"{sys.executable} -m pytest -q", test_timeout_s=60,
        max_context_files=8, file_chars=20000, output_chars=4000, push=False,
    ))
    agent._llm = llm
    return agent


def edits(summary, **files):
    return json.dumps({"summary": summary, "edits": [{"path": p, "content": c} for p, c in files.items()]})


def test_fix_is_applied_tested_and_committed(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    llm = ScriptedLLM(
        '{"files": ["calc.py", "test_calc.py", "not/in/repo.py"]}',
        edits("subtract instead of add", **{"calc.py": BUGGY.replace("subtract", "x")}),  # no-op: still wrong
        '```json\n{"files": ["calc.py"]}\n```',
        edits("fix add() to add", **{"calc.py": "def add(a, b):\n    return a + b\n"}),
    )
    agent = make_agent(repo, tmp_path, llm)

    first = asyncio.run(agent.run_iteration(7, "Issue #7: add() returns the wrong result", 1))
    assert not first.tests_passed and "assert" in first.test_output
    assert "--- not/in/repo.py ---" not in llm.prompts[1]  # only real repo files are sent

    second = asyncio.run(agent.run_iteration(7, "Issue #7: add() returns the wrong result", 2))
    assert second.tests_passed and second.branch == "jev/issue-7"
    assert "previous attempt's tests failed" in llm.prompts[3]
    assert "1 file changed" in second.diff_stat
    log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=tmp_path / "work" / "issue-7",
                         capture_output=True, text=True).stdout
    assert "fix add() to add" in log


def test_edits_outside_the_repo_are_rejected(repo, tmp_path):
    llm = ScriptedLLM('{"files": []}', edits("escape", **{"../../outside.py": "x = 1\n"}))
    agent = make_agent(repo, tmp_path, llm)
    with pytest.raises(ValueError, match="outside the repo"):
        asyncio.run(agent.run_iteration(8, "Issue #8", 1))
    assert not (tmp_path / "outside.py").exists()


def test_parse_json_handles_prose_and_fences():
    assert parse_json('Sure! ```json\n{"a": 1}\n``` done') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")
