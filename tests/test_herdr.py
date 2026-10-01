import pytest

import herdr


def _fake(monkeypatch, workspaces: list[dict]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    async def run(*args: str) -> dict:
        calls.append(args)
        if args[:2] == ("workspace", "list"):
            return {"workspaces": workspaces}
        if args[:2] == ("workspace", "create"):
            pane = {"pane_id": "w9:p1"}
            return {
                "workspace": {"workspace_id": "w9"},
                "tab": {"tab_id": "w9:t1"},
                "root_pane": pane,
            }
        if args[:2] == ("tab", "create"):
            return {"tab": {"tab_id": "w1:t5"}, "root_pane": {"pane_id": "w1:p5"}}
        if args[:2] == ("agent", "start"):
            raise herdr.HerdrError("agent_not_ready: blocked during startup")
        return {}

    monkeypatch.setattr(herdr, "run", run)
    return calls


async def test_failed_start_closes_its_tab(monkeypatch) -> None:
    calls = _fake(monkeypatch, [{"workspace_id": "w1", "label": "home"}])
    with pytest.raises(herdr.HerdrError):
        await herdr.start("do it", "/x", "claude", "home")
    assert calls[-1] == ("tab", "close", "w1:t5")


async def test_failed_start_closes_a_workspace_it_made(monkeypatch) -> None:
    calls = _fake(monkeypatch, [])
    with pytest.raises(herdr.HerdrError):
        await herdr.start("do it", "/x", "claude", "home")
    assert calls[-1] == ("workspace", "close", "w9")


async def test_a_stalled_prompt_is_sent_with_enter(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def run(*args: str) -> dict:
        calls.append(args)
        if args[:2] == ("agent", "prompt"):
            raise herdr.HerdrError("agent_prompt_stalled: no state change")
        return {}

    monkeypatch.setattr(herdr, "run", run)
    await herdr.submit("w1:p5", "do it")
    assert calls[-1] == ("agent", "send-keys", "w1:p5", "enter")


async def test_a_resumed_session_starts_on_its_id(monkeypatch) -> None:
    calls = _fake(monkeypatch, [{"workspace_id": "w1", "label": "home"}])

    async def start(*args: str) -> dict:
        calls.append(args)
        return {}

    async def get(pane_id: str) -> herdr.Agent:
        return herdr.Agent(pane_id, "Essay", "/x", "home", "idle", False, "s1")

    real = herdr.run
    monkeypatch.setattr(
        herdr, "run", lambda *a: start(*a) if a[:2] == ("agent", "start") else real(*a)
    )
    monkeypatch.setattr(herdr, "get", get)
    await herdr.start("", "/x", "claude", "home", resume="s1")
    assert calls[-1][-3:] == ("--", "--resume", "s1")
    # nothing is typed into a session reopened with no prompt
    assert not any(c[:2] == ("agent", "prompt") for c in calls)
