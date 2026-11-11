"""Keeps persistent MCP client sessions open (one background event loop) so the
synchronous harness can list and call MCP tools cheaply."""
import asyncio
import json
import sys
import threading
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Registered (approved) MCP servers. The agent can only use tools from these.
REGISTERED_SERVERS = {
    "market-data": StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_servers.market_data"]),
}


class McpHub:
    def __init__(self, servers=REGISTERED_SERVERS):
        self._servers = servers
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, dict] = {}   # tool name -> {server, schema...}
        self._run(self._connect())

    def _run(self, coro, timeout=60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _connect(self):
        for name, params in self._servers.items():
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[name] = session
            for t in (await session.list_tools()).tools:
                self.tools[t.name] = {"server": name, "name": t.name,
                                      "description": t.description or "",
                                      "input_schema": t.inputSchema}

    def anthropic_tool_specs(self) -> list[dict]:
        return [{"name": t["name"], "description": f"[MCP:{t['server']}] {t['description']}",
                 "input_schema": t["input_schema"]} for t in self.tools.values()]

    def call(self, name: str, args: dict, timeout: float = 45):
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' is not registered")
        session = self.sessions[self.tools[name]["server"]]
        res = self._run(session.call_tool(name, args), timeout)
        if res.isError:
            raise RuntimeError(" ".join(getattr(c, "text", "") for c in res.content))
        if getattr(res, "structuredContent", None):
            sc = res.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


_hub: McpHub | None = None
_lock = threading.Lock()


def get_hub() -> McpHub:
    global _hub
    with _lock:
        if _hub is None:
            _hub = McpHub()
        return _hub
