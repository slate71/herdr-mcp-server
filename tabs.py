"""The tabs this server opened: which it may close, and closing the ones left idle."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import herdr
from config import settings

logger = logging.getLogger("herdr-mcp-server.tabs")

# how often idle tabs are looked for
_SWEEP_SECONDS = 300


class Tabs:
    """Each opened tab by its pane, with the session in it, kept across restarts."""

    def __init__(self) -> None:
        self._path = settings.tabs_file
        self._opened: dict[str, dict[str, str]] = {}
        if self._path.exists():
            try:
                self._opened = json.loads(self._path.read_text())
            except ValueError:
                logger.exception("dropping unreadable tabs in %s", self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._opened))

    def add(self, agent: herdr.Agent) -> None:
        self._opened[agent.pane_id] = {
            "tab_id": agent.tab_id,
            "session_id": agent.session_id,
        }
        self._save()

    def opened(self, agent: herdr.Agent) -> bool:
        """Whether this server opened the agent's tab; a pane herdr reused is not it."""
        entry = self._opened.get(agent.pane_id)
        return entry is not None and entry["session_id"] in ("", agent.session_id)

    async def close(self, agent: herdr.Agent) -> None:
        await herdr.close(agent.tab_id)
        self._opened.pop(agent.pane_id, None)
        self._save()
        logger.info("closed %s (session %s)", agent.pane_id, agent.session_id)

    async def run(self) -> None:
        if settings.close_after <= 0:
            return
        while True:
            await asyncio.sleep(_SWEEP_SECONDS)
            try:
                await self._sweep()
            except Exception:
                logger.exception("closing idle tabs failed")

    async def _sweep(self) -> None:
        live = {a.pane_id: a for a in await herdr.agents()}
        cutoff = time.time() - settings.close_after * 3600
        for pane_id in list(self._opened):
            agent = live.get(pane_id)
            if agent is None or not self.opened(agent):
                # closed in herdr, or the pane id now belongs to someone else's session
                self._opened.pop(pane_id, None)
                self._save()
                continue
            if not self._opened[pane_id]["session_id"] and agent.session_id:
                # herdr names the session a moment after the tab opens
                self.add(agent)
            # a blocked session waits for the person, so only a finished one goes
            last = agent.transcript.mtime
            if agent.status in ("idle", "done") and 0 < last < cutoff:
                await self.close(agent)
