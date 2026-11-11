"""Harness loop with a scripted fake model (no API key needed)."""
from types import SimpleNamespace as NS

from app.harness import agent


class Block(NS):
    def model_dump(self, **_):
        return dict(vars(self))


def _resp(*blocks):
    return NS(content=list(blocks), stop_reason="tool_use", usage=None)


def _answer(id_="z"):
    return Block(type="tool_use", id=id_, name="answer",
                 input={"text": "done", "sources": [], "assumptions": []})


def test_debug_then_answer(tmp_db, monkeypatch):
    calls = {"n": 0}

    class C:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _resp(Block(type="tool_use", id="t1", name="run_python", input={"code": "print(1/0)"}))
            if calls["n"] == 2:
                assert "ZeroDivisionError" in kw["messages"][-1]["content"][0]["content"]
                return _resp(Block(type="tool_use", id="t2", name="run_python", input={"code": "print(2+2)"}))
            return _resp(_answer())

    monkeypatch.setattr(agent, "_client", lambda: C())
    from app.sandbox.runner import run_code
    rid = tmp_db.create_run({})
    stages = []
    out = agent.agent_loop(run_id=rid, agent="orchestrator", model="m", system="s",
                           messages=[{"role": "user", "content": "go"}], tools=[agent.RUN_PYTHON, agent.ANSWER],
                           handlers={"run_python": lambda a: run_code(a["code"])}, final_tool="answer",
                           max_steps=5, on_step=lambda s, label: stages.append(label))
    assert out["text"] == "done"
    assert stages == ["Running analysis code", "Running analysis code"]
    kinds = [e["kind"] for e in tmp_db.get_run(rid)["events"]]
    assert kinds.count("tool_call") == 2 and "final" in kinds


def test_unregistered_tool_is_blocked(tmp_db, monkeypatch):
    class C:
        def __init__(self):
            self.messages, self.n = self, 0

        def create(self, **kw):
            self.n += 1
            if self.n == 1:
                return _resp(Block(type="tool_use", id="x", name="place_order", input={}))
            assert kw["messages"][-1]["content"][0]["is_error"]
            return _resp(_answer())

    monkeypatch.setattr(agent, "_client", lambda: C())
    rid = tmp_db.create_run({})
    agent.agent_loop(run_id=rid, agent="o", model="m", system="s", messages=[{"role": "user", "content": "go"}],
                     tools=[], handlers={}, final_tool="answer", max_steps=3)
    assert any(e["kind"] == "tool_error" for e in tmp_db.get_run(rid)["events"])


def test_sandbox_run_limit_enforced(tmp_db, monkeypatch):
    monkeypatch.setattr(agent.settings, "max_sandbox_runs", 1)

    class C:
        def __init__(self):
            self.messages, self.n = self, 0

        def create(self, **kw):
            self.n += 1
            if self.n <= 2:
                return _resp(Block(type="tool_use", id=f"r{self.n}", name="run_python", input={"code": "print(1)"}))
            assert "limit" in kw["messages"][-1]["content"][0]["content"]
            return _resp(_answer())

    monkeypatch.setattr(agent, "_client", lambda: C())
    rid = tmp_db.create_run({})
    agent.agent_loop(run_id=rid, agent="o", model="m", system="s", messages=[{"role": "user", "content": "go"}],
                     tools=[agent.RUN_PYTHON], handlers={"run_python": lambda a: {"status": "ok"}},
                     final_tool="answer", max_steps=5)


def test_history_keeps_context_and_alternates(tmp_db):
    cid = tmp_db.create_conversation("AAPL")
    tmp_db.add_message(cid, "user", "Is AAPL in an uptrend?")
    tmp_db.add_message(cid, "assistant", {"text": "Yes, above its 200-day average.",
                                          "charts": [{"kind": "price", "ticker": "AAPL", "period": "1y"}]})
    tmp_db.add_message(cid, "user", "Now compare it with MSFT")
    msgs = agent.history_messages(cid, "Now compare it with MSFT")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert "200-day" in msgs[1]["content"] and "AAPL" in msgs[1]["content"]
    assert msgs[-1]["content"] == "Now compare it with MSFT"
