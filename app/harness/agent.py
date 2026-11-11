"""The harness (PMH-8, PMH-12, PMH-13): a multi-turn conversational agent that
coordinates the model, context, permissions and the analysis workflow.

Flow per user message: load recent conversation turns -> tool-use loop
(MCP data tools, Python sandbox with PTC, research sub-agents) -> structured
`answer` with text, charts, tables, sources and assumptions. Every step is
written to the audit log and reported as progress."""
import json
from typing import Callable

from app.config import settings
from app.harness import audit
from app.harness.context import clip, maybe_compact
from app.harness.llm import get_client
from app.harness.mcp_hub import get_hub
from app.harness.security import ptc_token
from app.sandbox.runner import run_code

RULES = """Rules (non-negotiable):
- Use only your registered tools. Never state a number that did not come from a tool or from code you ran.
- Do calculations in run_python, not in your head.
- Note the source and fetched_at time of every data point you rely on.
- Read-only: you analyse and explain; you cannot trade or change any account.
- Projections are ranges with a stated method, never single predictions. This is analysis, not financial advice."""

SYSTEM = f"""You are an AI stock market assistant. You bring market data, company research and analysis
together in a conversation. Answer the user's latest message, using earlier turns for context.

Tools:
- Quick facts: get_quote, get_company_profile, get_technical_summary, get_earnings, get_news,
  get_financial_statements, get_price_history.
- run_python: an isolated sandbox with pandas and numpy plus helper modules:
    from ptc import call_tool, prices   # prices(ticker, period) -> OHLCV DataFrame
    import ta     # sma, ema, rsi, macd, bollinger, atr, summary(df)
    import perf   # total_return, cagr, volatility, max_drawdown, sharpe, compare({{t: close}}),
                  # correlation, what_if_invested(close, amount, start), project_range(close, amount, years)
    import fa     # ratios(income, balance, cashflow, price, shares), growth(list)
  Use it for comparisons, what-if scenarios, projections and any multi-step calculation. Print compact JSON.
  If code fails, read the error, fix it and run again.
- delegate_research: hand a narrow, self-contained task (e.g. headline review) to a cheaper sub-agent.

Finish by calling `answer` exactly once:
- text: a clear explanation in plain language (short paragraphs, markdown allowed), defining any jargon.
- charts: request charts instead of pasting data. kind "price" (one ticker; overlays from sma_20, sma_50,
  sma_200, ema_20, bollinger; panes from rsi, macd) or kind "comparison" (2-5 tickers, normalised to 100).
- tables: small tables of the key numbers you computed (financials, comparisons, scenario results).
- sources and assumptions: always filled in.

{RULES}"""

SUB_SYSTEM = f"""You are a research sub-agent. Complete only the task you are given with your tools,
then call return_findings with a concise factual summary including sources and timestamps.
Give no investment recommendations.

{RULES}"""

RUN_PYTHON = {
    "name": "run_python",
    "description": "Run Python 3 in an isolated sandbox (time and memory limited, no secrets). pandas, numpy and "
                   "the ptc/ta/perf/fa helper modules are available. Print results. Returns stdout, stderr, status.",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}
DELEGATE = {
    "name": "delegate_research",
    "description": "Give a narrow research task to a sub-agent with data and sandbox tools. Returns its findings.",
    "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
}
ANSWER = {
    "name": "answer",
    "description": "Send the final answer to the user. Call once, at the end.",
    "input_schema": {"type": "object", "properties": {
        "text": {"type": "string"},
        "charts": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["price", "comparison"]},
            "title": {"type": "string"},
            "ticker": {"type": "string"},
            "tickers": {"type": "array", "items": {"type": "string"}},
            "period": {"type": "string", "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y"]},
            "overlays": {"type": "array", "items": {"type": "string", "enum": [
                "sma_20", "sma_50", "sma_200", "ema_20", "bollinger", "rsi", "macd"]}}},
            "required": ["kind", "period"]}},
        "tables": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array"}}},
            "required": ["columns", "rows"]}},
        "sources": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string"}, "fetched_at": {"type": "string"}, "used_for": {"type": "string"}}}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    }, "required": ["text", "sources", "assumptions"]},
}
RETURN_FINDINGS = {
    "name": "return_findings",
    "description": "Return your findings and finish.",
    "input_schema": {"type": "object", "properties": {"findings": {"type": "string"}}, "required": ["findings"]},
}

STAGE_LABELS = {"run_python": "Running analysis code", "delegate_research": "Sub-agent researching",
                "get_price_history": "Fetching price history", "get_technical_summary": "Computing indicators",
                "get_financial_statements": "Reading financial statements", "get_earnings": "Reading earnings",
                "get_company_profile": "Reading company profile", "get_news": "Reading news",
                "get_quote": "Fetching quote"}


class LimitReached(Exception):
    pass


class Budget:
    """Per-answer permission limits (PMH-12)."""

    def __init__(self):
        self.tool_calls = 0
        self.sandbox_runs = 0

    def charge(self, tool: str):
        self.tool_calls += 1
        if self.tool_calls > settings.max_tool_calls:
            raise LimitReached(f"Tool-call limit ({settings.max_tool_calls}) reached. Answer with what you have.")
        if tool == "run_python":
            self.sandbox_runs += 1
            if self.sandbox_runs > settings.max_sandbox_runs:
                raise LimitReached(f"Sandbox-run limit ({settings.max_sandbox_runs}) reached. Answer with what you have.")


def _client():
    return get_client()


def _final_from_text(final_tool: str, text: str) -> dict:
    """Open models sometimes reply in plain text instead of calling the final tool."""
    if final_tool == "answer":
        return {"text": text, "charts": [], "tables": [], "sources": [],
                "assumptions": ["The model replied in plain text, so sources were not itemised; "
                                "see 'Show the steps' for the data used."]}
    return {"findings": text}


def agent_loop(*, run_id: str, agent: str, model: str, system: str, messages: list,
               tools: list[dict], handlers: dict[str, Callable[[dict], object]],
               final_tool: str, max_steps: int, budget: Budget | None = None,
               on_step: Callable[[int, str], None] | None = None) -> dict:
    client = _client()
    budget = budget or Budget()
    log = lambda kind, payload: audit.log(run_id, agent, kind, payload)  # noqa: E731
    log("start", {"model": model, "tools": [t["name"] for t in tools]})

    for step in range(max_steps + 1):
        force = step == max_steps
        resp = client.messages.create(
            model=model, max_tokens=4096, system=system, tools=tools, messages=messages,
            **({"tool_choice": {"type": "tool", "name": final_tool}} if force else {}),
        )
        log("model_response", {"step": step, "stop_reason": resp.stop_reason,
                               "usage": resp.usage.model_dump() if getattr(resp, "usage", None) else None,
                               "text": "".join(b.text for b in resp.content if b.type == "text")})
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            text = "".join(b.text or "" for b in resp.content if b.type == "text").strip()
            if text:  # some models (often smaller open-source ones) reply in plain text instead of the tool
                out = _text_as_final(final_tool, text)
                log("final", {**out, "note": "model replied in plain text; wrapped as the final answer"})
                return out
            messages.append({"role": "user", "content": f"Continue. When done, call {final_tool}."})
            continue

        results = []
        for u in uses:
            if u.name == final_tool:
                log("final", u.input)
                return u.input
            if on_step:
                on_step(step, STAGE_LABELS.get(u.name, f"Calling {u.name}"))
            log("tool_call", {"tool": u.name, "input": u.input})
            try:
                if u.name not in handlers:
                    raise PermissionError(f"Tool '{u.name}' is not allowed for this agent")
                budget.charge(u.name)
                out = handlers[u.name](u.input)
                log("tool_result", {"tool": u.name, "result": out})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": clip(out)})
            except Exception as e:  # errors go back to the model so it can adapt or debug
                log("tool_error", {"tool": u.name, "error": repr(e)})
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": f"ERROR: {e!r}", "is_error": True})
        messages.append({"role": "user", "content": results})
        messages = maybe_compact(client, messages, log)

    raise RuntimeError(f"{agent} did not finish")


def _text_as_final(final_tool: str, text: str) -> dict:
    if final_tool == "answer":
        return {"text": text, "charts": [], "tables": [], "sources": [],
                "assumptions": ["Answer returned as plain text; sources were not itemised by the model. "
                                "See the steps for the data actually fetched."]}
    return {"findings": text}


def history_messages(conversation_id: str | None, new_message: str) -> list:
    """Earlier turns as plain text (answers summarised), then the new message."""
    msgs: list = []
    if conversation_id:
        turns = [m for m in audit.get_messages(conversation_id) if m["role"] in ("user", "assistant")]
        # the new user message is already stored; exclude it and keep the last N turns
        turns = turns[:-1][-settings.history_turns * 2:]
        for m in turns:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["content"]})
            else:
                a = m["content"] or {}
                extra = []
                for t in a.get("tables", []) or []:
                    extra.append(f"[table: {t.get('title', '')} {json.dumps(t.get('rows', [])[:6])[:600]}]")
                for c in a.get("charts", []) or []:
                    extra.append(f"[chart: {c.get('kind')} {c.get('ticker') or c.get('tickers')} {c.get('period')}]")
                msgs.append({"role": "assistant", "content": (a.get("text", "") + "\n" + "\n".join(extra)).strip()})
    # API requires alternating roles starting with user
    cleaned: list = []
    for m in msgs:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1]["content"] += "\n\n" + m["content"]
        elif cleaned or m["role"] == "user":
            cleaned.append(dict(m))
    if cleaned and cleaned[-1]["role"] == "user":
        cleaned[-1]["content"] += "\n\n" + new_message
    else:
        cleaned.append({"role": "user", "content": new_message})
    return cleaned


def run_chat(run_id: str, conversation_id: str | None, message: str,
             progress: Callable[[str, float], None] | None = None) -> dict:
    progress = progress or (lambda s, p: None)
    progress("Connecting to data tools", 0.05)
    hub = get_hub()
    mcp_specs = hub.tool_specs()
    token = ptc_token(run_id)
    budget = Budget()

    def mcp_handler(name):
        return lambda args: hub.call(name, args)

    base = {t["name"]: mcp_handler(t["name"]) for t in mcp_specs}
    base["run_python"] = lambda args: run_code(args["code"], ptc_token=token)
    n_sub = {"n": 0}

    def delegate(args):
        n_sub["n"] += 1
        return agent_loop(run_id=run_id, agent=f"subagent-{n_sub['n']}", model=settings.subagent_model,
                          system=SUB_SYSTEM, messages=[{"role": "user", "content": args["task"]}],
                          tools=mcp_specs + [RUN_PYTHON, RETURN_FINDINGS], handlers=base,
                          final_tool="return_findings", max_steps=8, budget=budget)

    handlers = {**base, "delegate_research": delegate}
    tools = mcp_specs + [RUN_PYTHON, DELEGATE, ANSWER]

    def on_step(step, label):
        progress(label, min(0.1 + 0.8 * (step + 1) / (settings.max_agent_steps + 1), 0.9))

    progress("Planning the analysis", 0.1)
    out = agent_loop(run_id=run_id, agent="orchestrator", model=settings.main_model, system=SYSTEM,
                     messages=history_messages(conversation_id, message), tools=tools, handlers=handlers,
                     final_tool="answer", max_steps=settings.max_agent_steps, budget=budget, on_step=on_step)
    out.setdefault("charts", [])
    out.setdefault("tables", [])
    return out
