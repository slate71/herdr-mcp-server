"""Env-derived settings for the server."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # where the MCP server listens
    host: str
    port: int
    herdr_bin: str
    # the herdr workspace a started agent opens in, unless the call names another
    workspace_label: str
    # the folder a task starts in when none is named, and where names are looked up
    code_dir: Path
    claude_projects: Path
    # seconds a tool call waits for its task before answering "still running"
    watch_window: float
    # seconds a task has to stay settled before it counts as done or blocked
    settle: float
    poll: float
    # subscriptions waiting to call back, kept across restarts
    state_file: Path
    # hours a subscription waits for its task before it is dropped
    subscription_ttl: float
    # characters of Claude Code's last message a result carries
    max_text: int
    # the tabs this server opened, kept across restarts so it can close them
    tabs_file: Path
    # hours a session it opened may sit idle before its tab is closed; 0 = never
    close_after: float


def load_settings() -> Settings:
    state = Path(os.getenv("HERDR_MCP_STATE_DIR", "~/.local/state/herdr-mcp-server"))
    return Settings(
        host=os.getenv("HERDR_MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("HERDR_MCP_PORT", "8960")),
        herdr_bin=os.getenv("HERDR_BIN") or shutil.which("herdr") or "herdr",
        workspace_label=os.getenv("HERDR_MCP_WORKSPACE", "herdr-mcp"),
        code_dir=Path(os.getenv("HERDR_MCP_CODE_DIR", "~/code")).expanduser(),
        claude_projects=Path("~/.claude/projects").expanduser(),
        watch_window=float(os.getenv("HERDR_MCP_WATCH_WINDOW", "60")),
        settle=float(os.getenv("HERDR_MCP_SETTLE", "4")),
        poll=float(os.getenv("HERDR_MCP_POLL", "2")),
        state_file=state.expanduser() / "subscriptions.json",
        subscription_ttl=float(os.getenv("HERDR_MCP_SUBSCRIPTION_TTL_HOURS", "24")),
        max_text=int(os.getenv("HERDR_MCP_MAX_TEXT", "800")),
        tabs_file=state.expanduser() / "tabs.json",
        close_after=float(os.getenv("HERDR_MCP_CLOSE_AFTER_HOURS", "6")),
    )


settings = load_settings()
