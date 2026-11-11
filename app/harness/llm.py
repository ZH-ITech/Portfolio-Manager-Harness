"""LLM provider layer.

The harness speaks one message format internally (system prompt + messages with
text / tool_use / tool_result blocks). This module lets that harness run on:

- "openai": any OpenAI-compatible Chat Completions API. Default: Groq's free tier
  with open-source models (e.g. Llama 3.3 70B). Also works with Ollama on your own
  machine (fully free and offline) or OpenRouter free models.
- "anthropic": the Claude API (paid), kept as an option.

Select with LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, MAIN_MODEL, SUBAGENT_MODEL."""
import json
import re
import time
import uuid
from types import SimpleNamespace

import httpx

from app.config import settings


class LLMTransientError(RuntimeError):
    """Rate limit, timeout or server error; safe to retry later."""


class LLMError(RuntimeError):
    """Request rejected (bad key, unknown model, bad request)."""


class Block(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False):
        d = dict(vars(self))
        return {k: v for k, v in d.items() if v is not None} if exclude_none else d


class Usage(SimpleNamespace):
    def model_dump(self, **_):
        return dict(vars(self))


# ---------- format conversion ----------
def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b if isinstance(b, dict) else b.model_dump() for b in content]


def to_openai_messages(system: str, messages: list) -> list:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        blocks = _blocks(m["content"])
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}}
                     for b in blocks if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            texts = []
            for b in blocks:
                if b.get("type") == "tool_result":
                    c = b.get("content", "")
                    if not isinstance(c, str):
                        c = json.dumps(c, default=str)
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": ("ERROR: " if b.get("is_error") and not c.startswith("ERROR") else "") + c})
                elif b.get("type") == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def to_openai_tools(tools: list) -> list:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema", {"type": "object"})}}
            for t in tools]


def from_openai_response(data: dict):
    msg = data["choices"][0]["message"]
    content = []
    if msg.get("content"):
        content.append(Block(type="text", text=msg["content"]))
    for call in msg.get("tool_calls") or []:
        raw = call["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {"_invalid_arguments": raw}
        content.append(Block(type="tool_use", id=call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                             name=call["function"]["name"], input=args if isinstance(args, dict) else {}))
    u = data.get("usage") or {}
    return SimpleNamespace(
        content=content,
        stop_reason="tool_use" if msg.get("tool_calls") else "end_turn",
        usage=Usage(input_tokens=u.get("prompt_tokens"), output_tokens=u.get("completion_tokens")),
    )


# ---------- OpenAI-compatible client with the harness's interface ----------
# ---------- recovery from malformed tool calls (common with open-weight models) ----------
_FAILED_CALL = re.compile(r'^\s*\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(.*?)\s*\}?\s*$', re.S)
TOOL_JSON_NUDGE = ("Your last tool call was rejected because its arguments were not valid JSON. "
                   "Call the tool again with arguments as a JSON object; inside strings escape newlines as \\n "
                   "and quotes as \\\".")


def _tool_use_failed(r: httpx.Response) -> str | None:
    """Return the model's raw failed generation if the provider rejected a malformed tool call."""
    if r.status_code != 400:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and err.get("code") == "tool_use_failed":
        return err.get("failed_generation") or ""
    return None


def salvage_tool_call(failed: str, tools: list | None):
    """Rebuild a tool call from a malformed generation when that is unambiguous:
    valid JSON arguments, or raw text for a tool that takes exactly one string field
    (e.g. run_python's `code`). Returns a response object or None."""
    m = _FAILED_CALL.match(failed or "")
    if not m:
        return None
    name, raw = m.group(1), m.group(2).strip()
    spec = next((t for t in tools or [] if t["name"] == name), None)
    if spec is None:
        return None
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            return None
    except json.JSONDecodeError:
        props = (spec.get("input_schema") or {}).get("properties", {})
        string_fields = [k for k, v in props.items() if v.get("type") == "string"]
        if len(props) != 1 or len(string_fields) != 1:
            return None
        text = raw
        if text.endswith('"'):
            text = text[:-1]
        if text.startswith('"'):
            text = text[1:]
        if "\n" not in text:   # code arrived on one line with escaped newlines
            text = text.replace("\\n", "\n")
        args = {string_fields[0]: text}
    return SimpleNamespace(content=[Block(type="tool_use", id=f"call_{uuid.uuid4().hex[:8]}", name=name, input=args)],
                           stop_reason="tool_use", usage=Usage(input_tokens=None, output_tokens=None),
                           salvaged=True)


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str = "", max_retries: int | None = None,
                 timeout: float | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = settings.llm_max_retries if max_retries is None else max_retries
        self.http = httpx.Client(timeout=timeout or settings.llm_timeout_s, transport=transport)
        self.messages = self   # so callers can use client.messages.create(...)

    def create(self, *, model, messages, system: str = "", max_tokens: int = 4096,
               tools=None, tool_choice=None, **_):
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": to_openai_messages(system, messages)}
        if tools:
            body["tools"] = to_openai_tools(tools)
            if tool_choice and tool_choice.get("type") == "tool":
                body["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
            else:
                body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        nudges = 0
        attempt = 0
        while True:
            last = attempt >= self.max_retries
            try:
                r = self.http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if last:
                    raise LLMTransientError(f"LLM unreachable at {self.base_url}: {e}") from e
                time.sleep(2 ** attempt)
                attempt += 1
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if last:
                    raise LLMTransientError(f"LLM busy or rate-limited (HTTP {r.status_code}): {r.text[:300]}")
                try:
                    delay = min(float(r.headers.get("retry-after", "")), 30)
                except ValueError:
                    delay = 2 ** (attempt + 1)
                time.sleep(delay)
                attempt += 1
                continue
            failed = _tool_use_failed(r)
            if failed is not None:
                fixed = salvage_tool_call(failed, tools)
                if fixed is not None:
                    return fixed
                if nudges < 2:
                    nudges += 1
                    body["messages"] = body["messages"] + [{"role": "user", "content": TOOL_JSON_NUDGE}]
                    continue
            if r.status_code >= 400:
                raise LLMError(f"LLM request failed (HTTP {r.status_code}): {r.text[:500]}")
            return from_openai_response(r.json())


def get_client():
    if settings.llm_provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    local = any(h in settings.llm_base_url for h in ("localhost", "127.0.0.1", "host.docker.internal", "ollama"))
    if not settings.llm_api_key and not local:
        raise LLMError("LLM_API_KEY is not set. Create a free key at console.groq.com (API Keys) and add "
                       "LLM_API_KEY=... to your .env file, then restart the server.")
    return OpenAICompatClient(settings.llm_base_url, settings.llm_api_key)


make_client = get_client
