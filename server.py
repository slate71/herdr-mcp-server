"""MCP server that lets an assistant drive the coding agents running in herdr.

A tool that sends work waits up to HERDR_MCP_WATCH_WINDOW seconds for it. A task still
running then is reported back later: the client names a webhook in the X-Callback-Url
and X-Callback-Token headers, and the server POSTs there once the task settles.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

import herdr
import match
import watch
from config import settings

logger = logging.getLogger("herdr-mcp-server")

subscriptions = watch.Subscriptions()

# the default folder as a person would say it
_code_dir = str(settings.code_dir).replace(str(Path.home()), "~", 1)

# a session idle longer than this is left out of the default list
_RECENT_HOURS = 6
_MAX_LISTED = 8

AgentRef = Annotated[
    str,
    Field(
        description="Which agent session: words from its title, its folder or workspace, "
        "its state (e.g. 'blocked'), 'the focused one', or its pane id (e.g. 'w3:p2'). "
        "Use the pane id from an earlier result when there is one."
    ),
]


# given the real host, FastMCP only guards against DNS rebinding when bound to localhost
mcp = FastMCP("herdr", host=settings.host, port=settings.port)


async def _find(ref: str) -> herdr.Agent:
    try:
        return match.resolve(ref, await herdr.agents())
    except match.Ambiguous as exc:
        raise ToolError(str(exc)) from exc


async def _follow(ctx: Context, agent: herdr.Agent, *, expect_work: bool) -> str:
    """Wait a while for the agent, then answer with how it ended or that it runs on."""
    # the first progress report lets the caller answer while this waits
    await ctx.report_progress(
        0, None, "Following it; the result comes as a later update."
    )
    try:
        settled = await watch.settle(
            agent.pane_id, settings.watch_window, expect_work=expect_work
        )
    except herdr.HerdrError as exc:
        raise ToolError(str(exc)) from exc
    if settled is not None:
        return watch.outcome(settled)
    request = ctx.request_context.request
    callback = request.headers.get("x-callback-url") if request is not None else None
    pane = f'agent "{agent.pane_id}"'
    if not callback:
        return f'"{agent.title}" is still working. Check later with get_herdr_agent({pane}).'
    subscriptions.add(agent, callback, request.headers.get("x-callback-token", ""))
    return (
        f'"{agent.title}" is still working. An update will arrive in this conversation '
        f"when it finishes or needs the person; follow-ups go to prompt_herdr_agent({pane})."
    )


def _folder(name: str) -> Path:
    if not name.strip():
        return settings.code_dir
    path = Path(name.strip()).expanduser()
    if not path.is_absolute():
        path = settings.code_dir / path
    if path.is_dir():
        return path
    for child in settings.code_dir.iterdir():
        if child.is_dir() and child.name.casefold() == name.strip().casefold():
            return child
    folders = ", ".join(
        sorted(c.name for c in settings.code_dir.iterdir() if c.is_dir())
    )
    raise ToolError(f"No folder {name!r}. Folders in {settings.code_dir}: {folders}")


@mcp.tool()
async def list_herdr_agents(
    query: Annotated[
        str,
        Field(
            description="Words to search every session by; empty lists the active ones."
        ),
    ] = "",
) -> str:
    """List the coding agent sessions (Claude Code) open in herdr on the computer.

    Without a query, only sessions working, blocked or active in the last few hours.
    """
    agents = await herdr.agents()
    if query.strip():
        shown = match.search(query, agents)[:_MAX_LISTED]
    else:
        cutoff = time.time() - _RECENT_HOURS * 3600
        recent = [
            a
            for a in agents
            if a.status in ("working", "blocked") or a.transcript.mtime > cutoff
        ]
        shown = sorted(recent, key=lambda a: -a.transcript.mtime)[:_MAX_LISTED]
    if not shown:
        return "No agent session matches." if query else "No agent session is active."
    lines = [f"- {a.describe()}" for a in shown]
    if hidden := len(agents) - len(shown):
        lines.append(f"({hidden} more sessions are open; search them with a query.)")
    return "\n".join(lines)


@mcp.tool()
async def get_herdr_agent(agent: AgentRef) -> str:
    """Show one agent session's state and the last thing it said."""
    return (await _find(agent)).describe(detail=True)


@mcp.tool()
async def watch_herdr_agent(ctx: Context, agent: AgentRef) -> str:
    """Follow an agent session until it finishes or needs the person."""
    return await _follow(ctx, await _find(agent), expect_work=False)


@mcp.tool()
async def prompt_herdr_agent(
    ctx: Context,
    agent: AgentRef,
    text: Annotated[
        str, Field(description="The message for the agent, in the person's words.")
    ],
) -> str:
    """Send a message to an agent session, then follow it. No confirmation needed.

    Use it for every follow-up about what a session found or did: after one checked
    the computer's memory, "why is it so high?" goes to that same session rather than
    being answered from your own knowledge. Also answers the question a session asks.
    A session blocked on a permission prompt takes answer_herdr_permission instead.
    """
    found = await _find(agent)
    try:
        if found.status != "blocked":
            await herdr.prompt(found.pane_id, text)
        elif choice := found.transcript.option(text):
            # the question's choices are numbered, and a digit picks one
            await herdr.keys(found.pane_id, str(choice))
        else:
            raise ToolError(watch.outcome(found))
    except herdr.HerdrError as exc:
        raise ToolError(str(exc)) from exc
    logger.info("prompted %s", found.pane_id)
    return f'Sent to "{found.title}".\n' + await _follow(ctx, found, expect_work=True)


@mcp.tool()
async def answer_herdr_permission(
    ctx: Context,
    agent: AgentRef,
    allow: Annotated[bool, Field(description="True to approve once, False to refuse.")],
) -> str:
    """Approve or refuse the permission prompt an agent session is blocked on.

    Say what it wants to run and get the person's yes before approving.
    """
    found = await _find(agent)
    if found.status != "blocked":
        raise ToolError(
            f'"{found.title}" is not waiting for permission; it is {found.status}.'
        )
    if found.transcript.asks_question:
        raise ToolError(watch.outcome(found))
    # the prompt's first choice is "Yes", and esc refuses and stops the turn
    await herdr.keys(found.pane_id, "enter" if allow else "esc")
    logger.info("%s permission in %s", "granted" if allow else "refused", found.pane_id)
    return await _follow(ctx, found, expect_work=allow)


@mcp.tool()
async def start_herdr_agent(
    ctx: Context,
    prompt: Annotated[
        str,
        Field(description="The task for the agent, complete enough to work on alone."),
    ],
    kind: Annotated[
        Literal["claude"],
        Field(description="Which coding agent: claude (Claude Code)."),
    ] = "claude",
    folder: Annotated[
        str,
        Field(
            description=f"Folder name under {_code_dir}, or a path; empty uses "
            f"{_code_dir} itself."
        ),
    ] = "",
    workspace: Annotated[
        str,
        Field(description="herdr workspace to open it in; empty uses the default."),
    ] = "",
) -> str:
    """Start a coding agent session that does something on the computer for the person.

    Use it whenever they ask for work on or with the computer, e.g. "用电脑查一下…" or
    "on my computer, check…": running commands, checking memory, disks or processes,
    searching the web from there, or working in a repo. Follow-ups then go to the same
    session with prompt_herdr_agent. Say the task and folder back to the person and get
    their yes before calling this.
    """
    cwd = _folder(folder)
    try:
        found = await herdr.start(
            prompt, str(cwd), kind, workspace.strip() or settings.workspace_label
        )
    except herdr.HerdrError as exc:
        raise ToolError(f"could not start {herdr.KINDS[kind]}: {exc}") from exc
    logger.info("started %s %s in %s", kind, found.pane_id, cwd)
    started = f"Started {herdr.KINDS[kind]} in {found.folder}, pane {found.pane_id}.\n"
    return started + await _follow(ctx, found, expect_work=True)


@mcp.tool()
async def stop_herdr_agent(agent: AgentRef) -> str:
    """Interrupt what an agent session is doing; it stays open for a later prompt."""
    found = await _find(agent)
    if found.status != "working":
        return f'"{found.title}" is not working; it is {found.status}.'
    await herdr.keys(found.pane_id, "esc")
    return f'Interrupted "{found.title}".'


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = mcp.streamable_http_app()
    serve_mcp = app.router.lifespan_context

    # FastMCP's own lifespan runs per MCP session; callbacks outlive every session
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with serve_mcp(app):
            task = asyncio.create_task(subscriptions.run())
            try:
                yield
            finally:
                task.cancel()

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host=settings.host, port=settings.port)
