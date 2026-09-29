from functools import partial

import anyio
import pytest

import herdr
import server
import watch


class _Ctx:
    async def report_progress(self, *args: object) -> None:
        pass


@pytest.mark.parametrize(("status", "stopped"), [("working", True), ("idle", False)])
async def test_a_cancelled_call_stops_its_session(monkeypatch, status, stopped) -> None:
    agent = herdr.Agent("w1:p1", "Essay", "/x", "home", status, False, "")
    sent: list[tuple[str, ...]] = []

    async def settle(*args: object, **kwargs: object) -> None:
        await anyio.sleep_forever()

    async def get(pane_id: str) -> herdr.Agent:
        return agent

    async def keys(pane_id: str, *names: str) -> None:
        sent.append((pane_id, *names))

    monkeypatch.setattr(watch, "settle", settle)
    monkeypatch.setattr(herdr, "get", get)
    monkeypatch.setattr(herdr, "keys", keys)
    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(server._follow, _Ctx(), agent, expect_work=False))
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()
    assert sent == ([("w1:p1", "esc")] if stopped else [])
