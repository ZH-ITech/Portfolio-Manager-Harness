"""MCP hub must work inside a Celery worker, where sys.stderr has no fileno()."""
import sys

from app.harness import mcp_hub


class LoggingProxyLike:
    """Mimics celery.utils.log.LoggingProxy: writable, but no file descriptor."""

    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def fileno(self):
        raise AttributeError("'LoggingProxy' object has no attribute 'fileno'")


def test_errlog_falls_back_to_real_stream(monkeypatch):
    monkeypatch.setattr(sys, "stderr", LoggingProxyLike())
    stream = mcp_hub._errlog()
    assert stream.fileno() >= 0


def test_hub_starts_mcp_server_under_logging_proxy(monkeypatch):
    monkeypatch.setattr(sys, "stderr", LoggingProxyLike())
    hub = mcp_hub.McpHub()
    assert {"get_quote", "get_price_history", "get_technical_summary"} <= set(hub.tools)
