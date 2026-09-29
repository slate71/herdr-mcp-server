import pytest

import match
from herdr import Agent


def _agent(
    pane: str, title: str, cwd: str, status: str = "idle", focused: bool = False
) -> Agent:
    return Agent(pane, title, cwd, cwd.rsplit("/", 1)[-1], status, focused, "")


TASKS = [
    _agent("w1:p1", "History view and restore", "/x/home-assistant-mcp-agent"),
    _agent(
        "w1:p2",
        "Home assistant MCP recommendations",
        "/x/home-assistant-mcp-agent",
        "working",
        True,
    ),
    _agent("w2:p1", "agents-js issue 2533", "/x/agents-js", "blocked"),
    _agent("w3:p1", "longc/session-persistence sixth pass", "/x/agents"),
    _agent("w3:p2", "longc/session-persistence review-and-fix pass 5", "/x/agents"),
    _agent("w4:p1", "空调温控2 setpoint tuning strategy", "/x"),
]


@pytest.mark.parametrize(
    ("query", "pane"),
    [
        ("w3:p2", "w3:p2"),
        ("the agents-js issue", "w2:p1"),
        ("home assistant mcp", "w1:p2"),
        ("the one on my screen", "w1:p2"),
        ("the blocked one", "w2:p1"),
        ("session persistence sixth", "w3:p1"),
        ("空调", "w4:p1"),
    ],
)
def test_resolve(query: str, pane: str) -> None:
    assert match.resolve(query, TASKS).pane_id == pane


def test_resolve_asks_when_two_fit() -> None:
    with pytest.raises(match.Ambiguous, match="ask which one"):
        match.resolve("session persistence", TASKS)


def test_resolve_names_a_missing_state() -> None:
    with pytest.raises(match.Ambiguous, match="No session is done"):
        match.resolve("the done one", TASKS)


def test_search_by_state() -> None:
    assert [t.pane_id for t in match.search("blocked", TASKS)] == ["w2:p1"]
