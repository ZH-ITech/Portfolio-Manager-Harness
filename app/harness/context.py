"""Context management: truncating large tool results and compacting history."""
import json

from app.config import settings


def clip(obj, limit: int | None = None) -> str:
    limit = limit or settings.tool_result_char_limit
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars; full result is in the audit log]"


def size(messages: list) -> int:
    return len(json.dumps(messages, default=str))


def _render(messages: list) -> str:
    parts = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            parts.append(f"{m['role']}: {content}")
            continue
        for b in content:
            b = b if isinstance(b, dict) else b.model_dump()
            if b.get("type") == "text":
                parts.append(f"{m['role']}: {b['text']}")
            elif b.get("type") == "tool_use":
                parts.append(f"tool_call {b['name']}({json.dumps(b['input'])[:500]})")
            elif b.get("type") == "tool_result":
                parts.append(f"tool_result: {str(b.get('content'))[:1500]}")
    return "\n".join(parts)


def maybe_compact(client, messages: list, run_log) -> list:
    """If history is over budget, summarise the middle and keep the original
    request plus the most recent assistant/tool exchange. Keeps tool_use /
    tool_result pairs intact so the API accepts the history."""
    if size(messages) < settings.compaction_char_budget or len(messages) < 6:
        return messages
    k = len(messages) - 4
    while k > 1 and messages[k]["role"] != "assistant":
        k += 1
    if k >= len(messages) or k <= 1:
        return messages
    middle = messages[1:k]
    resp = client.messages.create(
        model=settings.subagent_model, max_tokens=1200,
        system="Summarise this agent work log for the same agent to continue. Keep every number, "
               "ticker, data timestamp, source, intermediate conclusion and open question. Be terse.",
        messages=[{"role": "user", "content": _render(middle)}],
    )
    summary = "".join(b.text for b in resp.content if b.type == "text")
    first = messages[0]["content"]
    first_text = first if isinstance(first, str) else _render([messages[0]])
    run_log("compaction", {"dropped_messages": len(middle), "summary_chars": len(summary)})
    return [{"role": "user",
             "content": f"{first_text}\n\n<compacted_context>\n{summary}\n</compacted_context>"}] + messages[k:]
