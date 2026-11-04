"""stream_chat must always end in an answer: past its reasoning budget the model answers with thinking off,
building on the reasoning so far."""
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


MSGS = [dict(role="system", content="reguli"), dict(role="user", content="SURSE: ... ÎNTREBARE: x?")]


def test_endless_reasoning_falls_back_to_answer_built_on_it(monkeypatch):
    bodies = []
    endless = [dict(thinking="hm ")] * 49 + [dict(thinking="concluzie")]
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([endless, [dict(content="Răspuns [S1]")]], bodies))
    out = list(llm.Runtime().stream_chat("m", MSGS, think_budget=10))
    assert out[0] == ("budget", "10") and ("retry", "") in out and out[-1] == ("token", "Răspuns [S1]")
    assert sum(k == "thinking" for k, _ in out) == 10  # stopped at the budget, not after all 50
    # Thinking off must be sent explicitly: left out, Qwen3.8 reasons by default and the retry reasoned again.
    assert bodies[0]["think"] is True and bodies[1]["think"] is False
    assert "NOTELE TALE DE ANALIZĂ" in bodies[1]["messages"][-1]["content"] and "hm" in bodies[1]["messages"][-1]["content"]
    assert bodies[1]["messages"][0] == MSGS[0]


def test_reasoning_then_answer_needs_no_retry(monkeypatch):
    bodies = []
    reply = [dict(thinking="a")] * 5 + [dict(content="Gata")]
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([reply], bodies))
    out = list(llm.Runtime().stream_chat("m", MSGS, think_budget=10))
    assert len(bodies) == 1 and out[-1] == ("token", "Gata") and ("retry", "") not in out


def test_reasoning_that_ends_without_answer_retries(monkeypatch):
    bodies = []
    monkeypatch.setattr(llm.httpx, "stream", fake_stream([[dict(thinking="a")] * 3, [dict(content="X")]], bodies))
    out = list(llm.Runtime().stream_chat("m", MSGS, think_budget=10))
    assert len(bodies) == 2 and out[-1] == ("token", "X") and bodies[1]["think"] is False


def test_budget_shrinks_with_long_prompts():
    bodies = []
    r = llm.Runtime()
    big = [dict(role="user", content="x" * 3 * 10000)]  # ~10000 tokens of sources
    llm.httpx.stream, orig = fake_stream([[dict(content="ok")]], bodies), llm.httpx.stream
    try:
        out = list(r.stream_chat("m", big))
    finally:
        llm.httpx.stream = orig
    budget = int(out[0][1])
    assert 1500 <= budget < r.MAX_THINK and 10000 + budget + r.ANSWER_ROOM <= r.CHAT_CTX + 1500
