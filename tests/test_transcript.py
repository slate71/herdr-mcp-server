import dataclasses
import json

import transcript
from config import settings


def _write(tmp_path, monkeypatch, lines: list[dict]) -> None:
    folder = tmp_path / "-x"
    folder.mkdir()
    (folder / "s1.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    moved = dataclasses.replace(settings, claude_projects=tmp_path)
    monkeypatch.setattr(transcript, "settings", moved)


def _say(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _prompt(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def _call(call_id: str, name: str, args: dict) -> dict:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
    return {"type": "assistant", "message": {"content": [block]}}


def _result(call_id: str) -> dict:
    block = {"type": "tool_result", "tool_use_id": call_id}
    return {"type": "user", "message": {"content": [block]}}


def test_last_text_is_this_turns(tmp_path, monkeypatch) -> None:
    _write(tmp_path, monkeypatch, [_prompt("a"), _say("old"), _prompt("b")])
    assert transcript.read("s1").last_text == ""


def test_sidechain_is_skipped(tmp_path, monkeypatch) -> None:
    side = _say("from a subagent") | {"isSidechain": True}
    _write(tmp_path, monkeypatch, [_prompt("a"), _say("mine"), side])
    assert transcript.read("s1").last_text == "mine"


def test_pending_permission(tmp_path, monkeypatch) -> None:
    lines = [
        _call("t1", "Read", {}),
        _result("t1"),
        _call("t2", "Bash", {"command": "git push"}),
    ]
    _write(tmp_path, monkeypatch, lines)
    log = transcript.read("s1")
    assert log.pending_text() == "needs approval to run Bash: git push"
    assert not log.asks_question


def test_question_choices(tmp_path, monkeypatch) -> None:
    question = {
        "question": "Cat or dog?",
        "options": [{"label": "Cat"}, {"label": "Dog"}],
    }
    _write(
        tmp_path,
        monkeypatch,
        [_call("t1", "AskUserQuestion", {"questions": [question]})],
    )
    log = transcript.read("s1")
    assert log.choices == ["Cat", "Dog"]
    assert log.option("dog") == 2
    assert log.option("2") == 2
    assert log.option("a hamster") is None
