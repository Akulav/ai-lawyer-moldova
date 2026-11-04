"""stream_chat must always end in an answer: a reasoning model that thinks past its budget is asked again without thinking."""
import contextlib
import json

from app import llm


class FakeResp:
    status_code = 200

    def __init__(self, events):
        self.events = events

    def iter_lines(self):
        for e in self.events:
            yield json.dumps(dict(message=e))


def fake_stream(replies, bodies):
    @contextlib.contextmanager
    def stream(method, url, json=None, timeout=None):
        bodies.append(json)
        yield FakeResp(replies[len(bodies) - 1])
    return stream


def test_endless_reasoning_falls_back_to_direct_answer(monkeypatch):
    bodies = []
    endless = [dict(thinking="hm ")] * 50
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([endless, [dict(content="Răspuns [S1]")]], bodies))
    out = list(llm.Runtime().stream_chat("m", [], think_budget=10))
    assert ("retry", "") in out and out[-1] == ("token", "Răspuns [S1]")
    assert sum(k == "thinking" for k, _ in out) == 11  # stopped right after the budget, not after all 50
    assert bodies[0]["think"] is True and "think" not in bodies[1]


def test_reasoning_then_answer_needs_no_retry(monkeypatch):
    bodies = []
    reply = [dict(thinking="a")] * 5 + [dict(content="Gata")]
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([reply], bodies))
    out = list(llm.Runtime().stream_chat("m", [], think_budget=10))
    assert len(bodies) == 1 and out[-1] == ("token", "Gata") and ("retry", "") not in out


def test_reasoning_that_ends_without_answer_retries(monkeypatch):
    bodies = []
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([[dict(thinking="a")] * 3, [dict(content="X")]], bodies))
    out = list(llm.Runtime().stream_chat("m", [], think_budget=10))
    assert len(bodies) == 2 and out[-1] == ("token", "X")
