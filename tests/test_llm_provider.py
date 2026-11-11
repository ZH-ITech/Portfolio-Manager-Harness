"""OpenAI-compatible provider (Ollama/Groq/OpenRouter) against a fake server."""
import json

import httpx
import pytest

from app.harness import agent
from app.harness.llm import LLMTransientError, OpenAICompatClient, to_openai_messages


def _server(replies, seen):
    it = iter(replies)

    def handler(req: httpx.Request):
        seen.append(json.loads(req.content))
        status, body = next(it)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def _tool_call(name, args, id_="c1", as_string=True):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": id_, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args) if as_string else args}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_history_translation():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "checking"},
                                              {"type": "tool_use", "id": "t1", "name": "get_quote", "input": {"ticker": "AAPL"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{\"price\": 1}"}]}]
    out = to_openai_messages("sys", msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"] == {"name": "get_quote", "arguments": "{\"ticker\": \"AAPL\"}"}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "{\"price\": 1}"}


def test_full_agent_loop_over_openai_api(tmp_db, monkeypatch):
    seen = []
    replies = [(200, _tool_call("run_python", {"code": "print(6*7)"})),
               (200, _tool_call("answer", {"text": "42", "sources": [], "assumptions": []}, "c2", as_string=False))]
    client = OpenAICompatClient("http://fake/v1", transport=_server(replies, seen))
    monkeypatch.setattr(agent, "_client", lambda: client)
    from app.sandbox.runner import run_code
    rid = tmp_db.create_run({})
    out = agent.agent_loop(run_id=rid, agent="o", model="qwen", system="s",
                           messages=[{"role": "user", "content": "go"}],
                           tools=[agent.RUN_PYTHON, agent.ANSWER],
                           handlers={"run_python": lambda a: run_code(a["code"])}, final_tool="answer", max_steps=4)
    assert out["text"] == "42"
    assert seen[0]["tools"][0]["function"]["name"] == "run_python" and seen[0]["model"] == "qwen"
    assert seen[1]["messages"][-1]["role"] == "tool" and "42" in seen[1]["messages"][-1]["content"]


def test_plain_text_reply_becomes_answer(tmp_db, monkeypatch):
    seen = []
    replies = [(200, {"choices": [{"finish_reason": "stop", "message": {"content": "AAPL looks strong."}}]})]
    client = OpenAICompatClient("http://fake/v1", transport=_server(replies, seen))
    monkeypatch.setattr(agent, "_client", lambda: client)
    rid = tmp_db.create_run({})
    out = agent.agent_loop(run_id=rid, agent="o", model="m", system="s", messages=[{"role": "user", "content": "x"}],
                           tools=[agent.ANSWER], handlers={}, final_tool="answer", max_steps=3)
    assert out["text"] == "AAPL looks strong." and out["assumptions"]


def test_forced_tool_choice_and_errors():
    seen = []
    client = OpenAICompatClient("http://fake/v1", api_key="k", max_retries=0,
                                transport=_server([(200, _tool_call("answer", {})), (429, {"error": "slow down"})], seen))
    client.messages.create(model="m", system="s", tools=[agent.ANSWER], messages=[{"role": "user", "content": "x"}],
                           tool_choice={"type": "tool", "name": "answer"})
    assert seen[0]["tool_choice"] == {"type": "function", "function": {"name": "answer"}}
    with pytest.raises(LLMTransientError):
        client.messages.create(model="m", messages=[{"role": "user", "content": "x"}])


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    from app.harness import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    seen = []
    client = OpenAICompatClient("http://fake/v1", max_retries=2,
                                transport=_server([(429, {"error": "slow"}), (200, _tool_call("answer", {"text": "ok"}))], seen))
    r = client.messages.create(model="m", messages=[{"role": "user", "content": "x"}], tools=[agent.ANSWER])
    assert len(seen) == 2 and r.content[0].name == "answer"


def test_bad_request_is_not_retried():
    from app.harness.llm import LLMError
    seen = []
    client = OpenAICompatClient("http://fake/v1", max_retries=3, transport=_server([(400, {"error": "bad model"})], seen))
    with pytest.raises(LLMError):
        client.messages.create(model="nope", messages=[{"role": "user", "content": "x"}])
    assert len(seen) == 1


def test_missing_key_gives_clear_message(monkeypatch):
    from app.harness import llm
    monkeypatch.setattr(llm.settings, "llm_provider", "openai")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "")
    with pytest.raises(llm.LLMError, match="console.groq.com"):
        llm.get_client()
    monkeypatch.setattr(llm.settings, "llm_base_url", "http://localhost:11434/v1")   # Ollama needs no key
    assert isinstance(llm.get_client(), OpenAICompatClient)


# ---------- malformed tool calls from open-weight models (Groq: tool_use_failed) ----------
FAILED = ('{"name": "run_python", "arguments": import json, math\nforward_pe = 21.47552\n'
          'earnings_growth = 0.323 # 32.3%\npeg = forward_pe / (earnings_growth*100)\nprint(round(peg, 2))\n"}')


def _tool_failed(gen):
    return {"error": {"message": "Failed to parse tool call arguments as JSON", "type": "invalid_request_error",
                      "code": "tool_use_failed", "failed_generation": gen}}


def test_salvages_raw_code_for_run_python():
    seen = []
    client = OpenAICompatClient("http://fake/v1", max_retries=0, transport=_server([(400, _tool_failed(FAILED))], seen))
    r = client.messages.create(model="m", messages=[{"role": "user", "content": "x"}], tools=[agent.RUN_PYTHON])
    call = r.content[0]
    assert call.type == "tool_use" and call.name == "run_python" and len(seen) == 1
    assert call.input["code"].startswith("import json, math\nforward_pe")
    assert call.input["code"].rstrip().endswith("print(round(peg, 2))")
    from app.sandbox.runner import run_code
    assert run_code(call.input["code"])["stdout"].strip() == "0.66"   # salvaged code actually runs


def test_keeps_escaped_newlines_inside_string_literals():
    gen = '{"name": "run_python", "arguments": print("a\\nb")\nx = 1\n"}'
    r = __import__("app.harness.llm", fromlist=["salvage_tool_call"]).salvage_tool_call(gen, [agent.RUN_PYTHON])
    assert r.content[0].input["code"].startswith('print("a\\nb")\n')


def test_unsalvageable_call_is_retried_with_a_nudge():
    seen = []
    bad = '{"name": "answer", "arguments": {"text": "unterminated}'
    client = OpenAICompatClient("http://fake/v1", max_retries=0,
                                transport=_server([(400, _tool_failed(bad)), (200, _tool_call("answer", {"text": "ok"}))], seen))
    r = client.messages.create(model="m", messages=[{"role": "user", "content": "x"}], tools=[agent.ANSWER])
    assert r.content[0].input == {"text": "ok"} and len(seen) == 2
    assert "not valid JSON" in seen[1]["messages"][-1]["content"]


def test_gives_up_after_two_nudges():
    from app.harness.llm import LLMError
    seen = []
    bad = _tool_failed('{"name": "answer", "arguments": {broken')
    client = OpenAICompatClient("http://fake/v1", max_retries=0, transport=_server([(400, bad)] * 3, seen))
    with pytest.raises(LLMError, match="tool_use_failed"):
        client.messages.create(model="m", messages=[{"role": "user", "content": "x"}], tools=[agent.ANSWER])
    assert len(seen) == 3


# ---------- free-tier per-request token cap (Groq HTTP 413) ----------
GROQ_413 = {"error": {"message": "Request too large for model `openai/gpt-oss-120b` in organization `org_x` service tier "
                                 "`on_demand` on tokens per minute (TPM): Limit 8000, Requested 8283, please reduce your "
                                 "message size and try again.", "type": "tokens", "code": "rate_limit_exceeded"}}


def test_413_raises_request_too_large_without_waiting(monkeypatch):
    from app.harness import llm
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    seen = []
    client = OpenAICompatClient("http://fake/v1", max_retries=3, transport=_server([(413, GROQ_413)], seen))
    with pytest.raises(llm.LLMRequestTooLarge):
        client.messages.create(model="m", messages=[{"role": "user", "content": "x"}])
    assert len(seen) == 1 and slept == []      # no pointless waiting


def test_agent_shrinks_and_retries_when_request_too_large(tmp_db, monkeypatch):
    from types import SimpleNamespace as NS

    from app.harness.llm import LLMRequestTooLarge

    class Block(NS):
        def model_dump(self, **_):
            return dict(vars(self))

    big = "x" * 5000
    history = [{"role": "user", "content": "compare NVDA AMD INTC"}]
    for i in range(3):   # earlier tool rounds with large results
        history += [{"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "get_quote", "input": {}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": big}]}]
    calls = []

    class C:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            calls.append(kw)
            if kw.get("max_tokens") == 1200:       # the compaction summary call
                return NS(content=[Block(type="text", text="summary of earlier data")], stop_reason="end_turn", usage=None)
            if len(calls) == 1:
                raise LLMRequestTooLarge("Request too large ... Limit 8000, Requested 8283")
            return NS(content=[Block(type="tool_use", id="z", name="answer",
                                     input={"text": "done", "sources": [], "assumptions": []})],
                      stop_reason="tool_use", usage=None)

    monkeypatch.setattr(agent, "_client", lambda: C())
    rid = tmp_db.create_run({})
    out = agent.agent_loop(run_id=rid, agent="o", model="m", system="s", messages=history,
                           tools=[agent.ANSWER], handlers={}, final_tool="answer", max_steps=3)
    assert out["text"] == "done"
    retry = [c for c in calls if c.get("max_tokens") not in (1200,)][1]
    assert retry["max_tokens"] == agent.settings.llm_max_tokens // 2
    assert len(str(retry["messages"])) < len(str(history)) / 2       # request really got smaller
    assert any(e["kind"] == "context_shrink" for e in tmp_db.get_run(rid)["events"])
