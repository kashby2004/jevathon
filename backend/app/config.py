"""All tunables in one place. Thresholds are starting points (see docs §4), not measured values."""

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Mode(StrEnum):
    real = "real"
    fake = "fake"


# Keys each adapter needs in real mode. Checked at startup, not on first call.
REQUIRED_KEYS: dict[str, list[str]] = {
    "jev": ["typesafe_api_key"],
    "llm": ["gmi_api_key", "gmi_model"],
    "photon": ["photon_project_id", "photon_secret"],
    "browserbase": ["browserbase_api_key"],
    "github": ["github_token", "github_repo", "github_webhook_secret"],
    "agent": ["gmi_api_key", "gmi_model", "agent_repo_dir"],
}


class ConfigError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # --- adapter modes ---
    jev_mode: Mode = Mode.fake
    llm_mode: Mode = Mode.fake
    photon_mode: Mode = Mode.fake
    browserbase_mode: Mode = Mode.fake
    github_mode: Mode = Mode.fake
    agent_mode: Mode = Mode.fake

    # --- keys / endpoints ---
    typesafe_api_key: SecretStr | None = None
    jev_model: str = "jev-latest"
    gmi_api_key: SecretStr | None = None
    gmi_base_url: str = "https://api.gmi-serving.com/v1"
    gmi_model: str | None = None
    photon_project_id: str | None = None
    photon_secret: SecretStr | None = None
    photon_bridge_url: str = "http://localhost:4001"  # the TS process in photon-bridge/, not Spectrum directly
    browserbase_api_key: SecretStr | None = None
    # Internal docs sites searched with `site:` queries, comma-separated. Must be publicly reachable.
    internal_docs_sites: str = ""
    github_token: SecretStr | None = None
    github_repo: str | None = None
    github_webhook_secret: SecretStr | None = None
    github_base_branch: str = "main"
    # Author login CodeRabbit posts Issue Planner comments as. Verify against a real plan comment.
    coderabbit_login: str = "coderabbitai[bot]"
    frontend_origins: str = "http://localhost:5173"

    # --- Jev thresholds (docs §4) ---
    triage_confidence_floor: float = 0.6   # TypeSafe docs' suggested floor; below it, severity rounds up
    escalate_confidence: float = 0.85      # from the routing notes
    injection_noul_threshold: float = 0.5
    slop_noul_threshold: float = 0.5
    slop_impact_floor: float = 0.5  # min `impact` score to surface a primary_concern finding
    doc_relevance_threshold: float = 0.5
    reply_parse_confidence_floor: float = 0.6

    # --- coding agent (Ralph loop) ---
    agent_model: str | None = None          # GMI model for code fixes; falls back to gmi_model
    agent_repo_dir: Path | None = None      # local git clone of the repo the issues are about
    agent_work_dir: Path = BACKEND_DIR / ".agent-work"  # per-issue git worktrees
    agent_test_cmd: str = "python -m pytest -q"
    agent_test_timeout_s: int = 300
    agent_max_context_files: int = 8
    agent_file_chars: int = 20000           # per file sent to the model
    agent_output_chars: int = 4000          # test output kept per iteration (tail)
    agent_push: bool = False                # push the issue branch each iteration (needs GITHUB_MODE=real)

    # --- loop / routing policy ---
    max_loop_iterations: int = 8
    same_failure_limit: int = 3
    dev_cascade_size: int = 3
    max_open_assignments: int = 3
    reply_timeout_severe_s: int = 600
    reply_timeout_normal_s: int = 4 * 3600
    coderabbit_plan_wait_s: int = 300
    sweep_interval_s: int = 30

    # --- research ---
    research_topic_threshold: float = 0.5  # Jev Noul a catalog topic must clear to be researched
    research_max_topics: int = 3
    research_results_per_query: int = 3
    research_top_k: int = 3
    research_page_chars: int = 6000  # keeps Jev state focused; large irrelevant state hurts accuracy

    roster_path: Path = BACKEND_DIR / "data" / "devs.json"
    research_topics_path: Path = BACKEND_DIR / "data" / "research_topics.json"

    def modes(self) -> dict[str, Mode]:
        return {name: getattr(self, f"{name}_mode") for name in REQUIRED_KEYS}

    def validate_startup(self) -> list[str]:
        """Fail fast on real adapters missing keys. Returns fake adapters so callers can surface them."""
        missing = [
            f"{name}: {key.upper()}"
            for name, mode in self.modes().items()
            if mode is Mode.real
            for key in REQUIRED_KEYS[name]
            if not getattr(self, key)
        ]
        if missing:
            raise ConfigError("Real adapters missing config: " + ", ".join(missing))
        fakes = [name for name, mode in self.modes().items() if mode is Mode.fake]
        if fakes:
            log.warning("FAKE adapters active (not real integrations): %s", ", ".join(fakes))
        return fakes

    def internal_sites(self) -> list[str]:
        return [s.strip() for s in self.internal_docs_sites.split(",") if s.strip()]

    def reply_timeout_s(self, severe: bool) -> int:
        return self.reply_timeout_severe_s if severe else self.reply_timeout_normal_s
