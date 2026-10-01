import dataclasses
import time

import pytest

import herdr
import server
import tabs as tabs_module
from config import settings


def _agent(pane: str, status: str, session: str, workspace: str = "inbox"):
    return herdr.Agent(
        pane, "Essay", "/x", workspace, status, False, session, tab_id=f"{pane}-tab"
    )


@pytest.fixture
def tabs(tmp_path, monkeypatch):
    moved = dataclasses.replace(
        settings, tabs_file=tmp_path / "tabs.json", close_after=6
    )
    monkeypatch.setattr(tabs_module, "settings", moved)
    closed: list[str] = []

    async def close(tab_id: str) -> None:
        closed.append(tab_id)

    monkeypatch.setattr(herdr, "close", close)
    found = tabs_module.Tabs()
    found.closed = closed
    return found


async def test_sweep_closes_only_its_own_finished_tabs(tabs, monkeypatch) -> None:
    old = time.time() - 7 * 3600
    done, blocked, recent = (
        _agent("w1:p1", "idle", "a"),
        _agent("w1:p2", "blocked", "b"),
        _agent("w1:p3", "done", "c"),
    )
    reused = _agent("w1:p4", "idle", "someone-else")
    for agent in (done, blocked, recent, _agent("w1:p4", "idle", "d")):
        tabs.add(agent)
    mtimes = {"a": old, "b": old, "c": time.time(), "someone-else": old}

    async def agents():
        return [done, blocked, recent, reused]

    monkeypatch.setattr(herdr, "agents", agents)
    monkeypatch.setattr(
        herdr.Agent,
        "transcript",
        property(lambda a: herdr.transcript.Transcript(mtime=mtimes[a.session_id])),
    )
    await tabs._sweep()
    # a blocked session waits for the person, a recent one may get a follow-up, and a
    # pane herdr gave another session is not this server's to close
    assert tabs.closed == ["w1:p1-tab"]
    assert not tabs.opened(reused)
    assert tabs.opened(blocked) and tabs.opened(recent)


@pytest.mark.parametrize(
    ("agent", "error"),
    [
        (_agent("w1:p1", "working", "a"), "still working"),
        (_agent("w2:p1", "idle", "b", workspace="mine"), "not opened from here"),
    ],
)
async def test_close_refuses_a_working_or_foreign_session(
    tabs, monkeypatch, agent, error
) -> None:
    async def find(ref: str) -> herdr.Agent:
        return agent

    monkeypatch.setattr(server, "_find", find)
    monkeypatch.setattr(server, "tabs", tabs)
    monkeypatch.setattr(
        server, "settings", dataclasses.replace(settings, workspace_label="inbox")
    )
    with pytest.raises(server.ToolError, match=error):
        await server.close_herdr_agent("it")
    assert tabs.closed == []
