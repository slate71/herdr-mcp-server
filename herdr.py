"""The herdr CLI, and the coding agents it hosts in its panes."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

import transcript
from config import settings

# states in which an agent is waiting for someone, not working
SETTLED = ("idle", "done", "blocked")
# agent kinds whose transcript this server can read, by herdr's --kind, with their names
KINDS = {"claude": "Claude Code"}


class HerdrError(RuntimeError):
    """A herdr command failed; the message is herdr's own."""


async def run(*args: str) -> dict:
    """Run one herdr command and return its JSON ``result``."""
    proc = await asyncio.create_subprocess_exec(
        settings.herdr_bin,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        try:
            error = json.loads(err or out)["error"]
            raise HerdrError(f"{error['code']}: {error['message']}")
        except (ValueError, KeyError, TypeError):
            raise HerdrError((err or out).decode().strip() or f"herdr {args[0]} failed")
    return json.loads(out).get("result", {})


@dataclass
class Agent:
    """A coding agent session running in a herdr pane."""

    pane_id: str
    title: str
    cwd: str
    workspace: str
    status: str
    focused: bool
    session_id: str
    kind: str = "claude"

    @property
    def transcript(self) -> transcript.Transcript:
        return transcript.read(self.session_id)

    @property
    def folder(self) -> str:
        home = str(Path.home())
        return self.cwd.replace(home, "~", 1) if self.cwd.startswith(home) else self.cwd

    def describe(self, *, detail: bool = False) -> str:
        """One line naming the agent and its state, or with ``detail`` its last message."""
        log = self.transcript
        state = self.status
        if self.status in ("idle", "done") and log.interrupted:
            state = "interrupted"
        elif self.status == "blocked":
            state = f"blocked: {log.pending_text()}" if log.pending else "blocked"
        age = ""
        if log.mtime:
            minutes = int((time.time() - log.mtime) // 60)
            age = ", active just now" if minutes < 1 else f", active {minutes} min ago"
        line = (
            f'"{self.title}" ({KINDS[self.kind]}, pane {self.pane_id}) [{state}] '
            f"in {self.folder} (workspace {self.workspace}{age})"
        )
        if detail and log.last_text:
            line += f"\nLast message: {log.last_text}"
        return line


def _agent(agent: dict, labels: dict[str, str]) -> Agent:
    session = agent.get("agent_session") or {}
    return Agent(
        pane_id=agent["pane_id"],
        title=agent.get("terminal_title_stripped")
        or agent.get("name")
        or agent["pane_id"],
        cwd=agent.get("cwd") or "",
        workspace=labels.get(
            agent.get("workspace_id", ""), agent.get("workspace_id", "")
        ),
        status=agent.get("agent_status") or "unknown",
        focused=bool(agent.get("focused")),
        session_id=session.get("value") or "",
        kind=agent.get("agent") or "claude",
    )


async def _labels() -> dict[str, str]:
    workspaces = (await run("workspace", "list")).get("workspaces") or []
    return {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in workspaces}


async def agents() -> list[Agent]:
    """Every agent herdr hosts, across all workspaces, of a kind this server reads."""
    found, labels = await asyncio.gather(run("agent", "list"), _labels())
    return [
        _agent(a, labels) for a in found.get("agents") or [] if a.get("agent") in KINDS
    ]


async def get(pane_id: str) -> Agent | None:
    """The agent in one pane, or None once it has gone."""
    try:
        found, labels = await asyncio.gather(run("agent", "get", pane_id), _labels())
    except HerdrError:
        return None
    return _agent(found["agent"], labels)


async def start(prompt: str, cwd: str, kind: str, workspace: str) -> Agent:
    """Start an agent of ``kind`` on ``prompt`` in a new tab of ``workspace``."""
    workspaces = (await run("workspace", "list")).get("workspaces") or []
    existing = next((w for w in workspaces if w.get("label") == workspace), None)
    # tabs are created unlabelled, so herdr keeps naming them from what runs there
    if existing is None:
        created = await run(
            "workspace", "create", "--label", workspace, "--cwd", cwd, "--no-focus"
        )
        # a failed start leaves no shell behind: close what was made for it
        cleanup = ("workspace", "close", created["workspace"]["workspace_id"])
    else:
        created = await run(
            "tab", "create", "--workspace", existing["workspace_id"],
            "--cwd", cwd, "--no-focus",
        )  # fmt: skip
        cleanup = ("tab", "close", created["tab"]["tab_id"])
    pane_id = created["root_pane"]["pane_id"]
    # an agent name is required; the pane id makes it unique among live agents
    name = f"{kind}-" + pane_id.replace(":", "-").lower()
    try:
        await run("agent", "start", name, "--kind", kind, "--pane", pane_id)
    except HerdrError:
        await run(*cleanup)
        raise
    await run("agent", "prompt", pane_id, prompt)
    agent = await get(pane_id)
    if agent is None:
        raise HerdrError(f"the new {KINDS[kind]} session in {pane_id} went away")
    return agent


async def prompt(pane_id: str, text: str) -> None:
    await run("agent", "prompt", pane_id, text)


async def keys(pane_id: str, *names: str) -> None:
    await run("agent", "send-keys", pane_id, *names)
