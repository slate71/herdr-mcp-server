"""What a Claude Code session last said, read from its transcript on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from config import settings

# only the tail is read, so a long session costs the same as a short one
_TAIL_BYTES = 256 * 1024
# how Claude Code records that the person stopped its turn, e.g. with esc in the terminal
_INTERRUPTED = "[Request interrupted by user"


@dataclass
class Transcript:
    # the last thing Claude Code wrote to the person since their last prompt, clipped
    last_text: str = ""
    # the tool call still waiting for its result: {"name", "input"}
    pending: dict | None = None
    mtime: float = 0.0
    # the person stopped the latest turn before it finished
    interrupted: bool = False

    def pending_text(self) -> str:
        """The waiting call in words: the permission it needs or the question it asks."""
        if not self.pending:
            return ""
        name, args = self.pending["name"], self.pending.get("input") or {}
        if name == "AskUserQuestion":
            asked = []
            for q in args.get("questions") or []:
                options = " / ".join(o.get("label", "") for o in q.get("options") or [])
                asked.append(f"{q.get('question', '')} ({options})")
            return "asks " + "; ".join(asked)
        shown = args.get("command") or args.get("file_path") or args.get("url") or ""
        if not shown:
            shown = json.dumps(args, ensure_ascii=False)
        return f"needs approval to run {name}: {shown[:200]}"

    @property
    def asks_question(self) -> bool:
        return bool(self.pending and self.pending["name"] == "AskUserQuestion")

    @property
    def choices(self) -> list[str] | None:
        """The options of the one question asked; None for no question, or several."""
        if not self.asks_question:
            return None
        questions = self.pending["input"].get("questions") or []
        if len(questions) != 1:
            return None
        return [o.get("label", "") for o in questions[0].get("options") or []]

    def option(self, answer: str) -> int | None:
        """The 1-based number of the choice ``answer`` names, if it names one."""
        choices = self.choices or []
        said = answer.strip().casefold()
        if said.isdigit() and 1 <= int(said) <= len(choices):
            return int(said)
        exact = [i for i, c in enumerate(choices, 1) if c.casefold() == said]
        near = [
            i
            for i, c in enumerate(choices, 1)
            if said in c.casefold() or c.casefold() in said
        ]
        found = exact or near
        return found[0] if len(found) == 1 else None


def _path(session_id: str) -> Path | None:
    if not session_id:
        return None
    return next(settings.claude_projects.glob(f"*/{session_id}.jsonl"), None)


def cwd(session_id: str) -> str | None:
    """The folder a session ran in, which resuming it needs; None for no such session."""
    path = _path(session_id)
    if path is None:
        return None
    with path.open("rb") as f:
        for raw in f:
            try:
                folder = json.loads(raw).get("cwd")
            except ValueError:
                continue
            if folder:
                return folder
    return None


def read(session_id: str) -> Transcript:
    path = _path(session_id)
    if path is None:
        return Transcript()
    stat = path.stat()
    with path.open("rb") as f:
        f.seek(max(0, stat.st_size - _TAIL_BYTES))
        lines = f.read().splitlines()
    log = Transcript(mtime=stat.st_mtime)
    calls: dict[str, dict] = {}
    for raw in lines:
        try:
            line = json.loads(raw)
        except ValueError:
            continue  # the first line of the tail is usually cut
        # a subagent's lines are its own conversation, not the session's
        if line.get("isSidechain") or line.get("type") not in ("assistant", "user"):
            continue
        content = (line.get("message") or {}).get("content")
        if isinstance(content, str):
            # a prompt the person typed starts a turn, and the text said before it is old
            log.last_text, log.interrupted = "", False
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            kind = block.get("type")
            text = block.get("text") or ""
            if (
                kind == "text"
                and line["type"] == "user"
                and text.startswith(_INTERRUPTED)
            ):
                # the turn ends here, and a call it was in the middle of never returns
                log.interrupted = True
                calls.clear()
            elif kind == "text" and line["type"] == "assistant" and text:
                log.last_text = text.strip()
            elif kind == "tool_use":
                calls[block.get("id", "")] = block
            elif kind == "tool_result":
                calls.pop(block.get("tool_use_id", ""), None)
    if calls:
        call = list(calls.values())[-1]
        log.pending = {"name": call.get("name", ""), "input": call.get("input") or {}}
    if len(log.last_text) > settings.max_text:
        log.last_text = log.last_text[: settings.max_text - 1] + "…"
    return log
