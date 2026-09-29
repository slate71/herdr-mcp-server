"""Waiting on a agent, and calling back whoever subscribed once it settles."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass

import httpx

import herdr
from config import settings

logger = logging.getLogger("herdr-mcp-server.watch")

# how long input takes to show as work before the agent is taken as it is
_START_GRACE = 10.0


def outcome(agent: herdr.Agent) -> str:
    """A settled agent in words: what it finished with, or what it is waiting for."""
    log = agent.transcript
    pane = f'agent "{agent.pane_id}"'
    if agent.status == "blocked":
        waiting = log.pending_text() or "is waiting for input in herdr"
        text = f'"{agent.title}" {waiting}.'
        if log.choices:
            text += (
                f" Answer with prompt_herdr_agent({pane}), naming one of the choices."
            )
        elif log.asks_question:
            text += " It asks several questions at once; answer them in herdr."
        else:
            text += f" Ask the person, then call answer_herdr_permission({pane})."
    elif agent.status in ("idle", "done") and log.interrupted:
        # stopped on purpose, so sending it again would undo the person's stop
        text = (
            f'"{agent.title}" was interrupted in herdr before it finished. Tell the '
            "person, and send it more work only if they ask."
        )
    elif agent.status in ("idle", "done"):
        text = f'"{agent.title}" finished.'
    else:
        text = f'"{agent.title}" is {agent.status}.'
    if log.last_text:
        text += f"\nLast message: {log.last_text}"
    # the session holds what it found, so a follow-up is its question to answer
    return text + f"\nSend follow-ups about this to prompt_herdr_agent({pane})."


async def settle(
    pane_id: str, window: float, *, expect_work: bool
) -> herdr.Agent | None:
    """The agent once it has settled within ``window`` seconds, else None.

    With ``expect_work``, input was just sent, so a settled state only counts after the
    agent has been seen working or the grace period has passed.
    """
    start = time.monotonic()
    worked = not expect_work
    since: float | None = None
    while (now := time.monotonic()) - start < window:
        agent = await herdr.get(pane_id)
        if agent is None:
            raise herdr.HerdrError(f"the session in {pane_id} was closed")
        if agent.status == "working":
            worked, since = True, None
        elif agent.status in herdr.SETTLED and (worked or now - start > _START_GRACE):
            since = since or now
            if now - since >= settings.settle:
                return agent
        await asyncio.sleep(settings.poll)
    return None


@dataclass
class Subscription:
    pane_id: str
    title: str
    callback_url: str
    token: str
    created: float
    # set once the agent settles, so a retry sends what happened then
    event: str = ""
    state: str = ""
    attempts: int = 0
    next_try: float = 0.0


class Subscriptions:
    """Agents someone asked to hear back about, each called back once."""

    def __init__(self) -> None:
        self._path = settings.state_file
        self._subs: list[Subscription] = []
        self._since: dict[str, float] = {}
        if self._path.exists():
            try:
                self._subs = [
                    Subscription(**s) for s in json.loads(self._path.read_text())
                ]
            except (ValueError, TypeError):
                logger.exception("dropping unreadable subscriptions in %s", self._path)

    def add(self, agent: herdr.Agent, callback_url: str, token: str) -> None:
        # a newer ask about the same agent replaces the older one
        self._subs = [
            s
            for s in self._subs
            if (s.pane_id, s.callback_url) != (agent.pane_id, callback_url)
        ]
        self._subs.append(
            Subscription(agent.pane_id, agent.title, callback_url, token, time.time())
        )
        self._save()
        logger.info("subscribed %s to %s", callback_url, agent.pane_id)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(s) for s in self._subs]))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(settings.poll)
            if self._subs:
                try:
                    await self._check()
                except Exception:
                    logger.exception("checking subscriptions failed")

    async def _check(self) -> None:
        checking = list(self._subs)
        live = {t.pane_id: t for t in await herdr.agents()}
        now = time.time()
        keep: list[Subscription] = []
        for sub in checking:
            if not sub.event:
                if now - sub.created > settings.subscription_ttl * 3600:
                    logger.info("dropping %s, still not settled", sub.pane_id)
                    continue
                agent = live.get(sub.pane_id)
                if agent is None:
                    sub.event, sub.state = f'"{sub.title}" was closed.', "closed"
                elif agent.status in herdr.SETTLED:
                    since = self._since.setdefault(sub.pane_id, now)
                    if now - since < settings.settle:
                        keep.append(sub)
                        continue
                    sub.event, sub.state = outcome(agent), agent.status
                else:
                    self._since.pop(sub.pane_id, None)
                    keep.append(sub)
                    continue
            if now < sub.next_try:
                keep.append(sub)
                continue
            if await _deliver(sub):
                self._since.pop(sub.pane_id, None)
                continue
            sub.attempts += 1
            if sub.attempts > 10:
                logger.warning("giving up on calling %s back", sub.callback_url)
                continue
            sub.next_try = now + min(3600, 30 * 2**sub.attempts)
            keep.append(sub)
        # add() may have run while this awaited herdr or a callback
        added = [s for s in self._subs if not any(s is c for c in checking)]
        # and replaced one of those checked, so the newer of a pair wins
        newest = {(s.pane_id, s.callback_url): s for s in keep + added}
        self._subs = list(newest.values())
        self._save()


async def _deliver(sub: Subscription) -> bool:
    body = {
        "source": "Claude Code",
        "text": sub.event,
        "title": sub.title,
        "state": sub.state,
        "pane_id": sub.pane_id,
    }
    headers = {"Authorization": f"Bearer {sub.token}"} if sub.token else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(sub.callback_url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("calling back %s failed: %s", sub.callback_url, exc)
        return False
    if resp.status_code >= 300:
        logger.warning(
            "%s answered %s: %s", sub.callback_url, resp.status_code, resp.text
        )
        return False
    logger.info("called back %s about %s", sub.callback_url, sub.pane_id)
    return True
