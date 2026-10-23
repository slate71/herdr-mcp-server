"""Find the agent a spoken description means, among many open sessions."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from herdr import Agent

# words that mean "the session on my screen"
_FOCUSED = {"focused", "current", "screen", "this", "open", "front"}
# herdr states a description can name
_STATES = {"working", "blocked", "idle", "done"}
# words that carry no meaning in a description like "the one in agents"
_FILLER = {
    "the",
    "a",
    "an",
    "one",
    "task",
    "agent",
    "session",
    "in",
    "on",
    "my",
    "of",
    "about",
    "that",
    "is",
}


class Ambiguous(LookupError):
    """No agent, or more than one, fits the description; the message lists the options."""


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[\s/_\-.,:]+", text.casefold()) if w} - _FILLER


def _score(query: str, agent: Agent) -> float:
    words = _words(query)
    if not words:
        return 0.0
    # the person's own name for the tab is what they will say first
    title = f"{agent.label} {agent.title}".casefold()
    haystack = f"{title} {agent.folder} {agent.workspace} {agent.pane_id}".casefold()
    in_title = sum(1 for w in words if w in title) / len(words)
    # a folder or workspace is shared by many sessions, so it weighs less than a title
    anywhere = sum(1 for w in words if w in haystack) / len(words)
    # a close title match counts too, for titles no word splits cleanly, like CJK ones
    similar = SequenceMatcher(None, query.casefold(), title).ratio()
    return max(in_title, 0.8 * anywhere, similar)


def _rank(query: str, agents: list[Agent]) -> list[tuple[float, Agent]]:
    return sorted(((_score(query, t), t) for t in agents), key=lambda p: -p[0])


def search(query: str, agents: list[Agent]) -> list[Agent]:
    """The agents ``query`` fits, best first; a state word keeps only that state."""
    words = _words(query)
    if states := words & _STATES:
        agents = [t for t in agents if t.status in states]
        words -= _STATES
        if not words:
            return agents
    return [t for s, t in _rank(" ".join(words), agents) if s >= 0.4]


def resolve(query: str, agents: list[Agent]) -> Agent:
    """The one agent ``query`` names by title, folder, workspace, state, pane id or
    session id."""
    query = query.strip()
    if not agents:
        raise Ambiguous("No coding agent is open in herdr.")
    for agent in agents:
        if query and query in (agent.pane_id, agent.session_id):
            return agent
    # the tab's name, said exactly, is the person's own id for it
    named = [a for a in agents if a.label and a.label.casefold() == query.casefold()]
    if len(named) == 1:
        return named[0]
    words = _words(query)
    if words & _FOCUSED:
        focused = [t for t in agents if t.focused]
        if len(focused) == 1 and not (words - _FOCUSED):
            return focused[0]
    # a state word narrows to the sessions in that state, like "the blocked one"
    if states := words & _STATES:
        agents = [t for t in agents if t.status in states]
        if not agents:
            raise Ambiguous(f"No session is {' or '.join(sorted(states))}.")
        if len(agents) == 1:
            return agents[0]
        query = " ".join(words - _STATES)
    ranked = _rank(query, agents)
    best_score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score >= 0.5 and best_score - runner_up >= 0.15:
        return best
    close = [t for s, t in ranked[:4] if s >= max(0.3, best_score - 0.15)]
    if not close:
        raise Ambiguous(
            f'No agent session matches "{query}". Call list_herdr_agents to see them.'
        )
    options = "\n".join(f"- {t.describe()}" for t in close)
    raise Ambiguous(
        f'More than one session could be "{query}"; ask which one:\n{options}'
    )
