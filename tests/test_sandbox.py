import pytest

from app.sandbox.runner import HAS_RLIMITS, run_code


def test_runs_code_and_captures_output():
    r = run_code("import pandas as pd\nprint(pd.Series([1,2,3]).mean())")
    assert r["status"] == "ok", r
    assert r["stdout"].strip() == "2.0"


def test_reports_errors_for_debugging():
    r = run_code("1/0")
    assert r["status"] == "error"
    assert "ZeroDivisionError" in r["stderr"]


def test_enforces_timeout():
    r = run_code("while True: pass", timeout_s=2)
    assert r["status"] in ("timeout", "error")


@pytest.mark.skipif(not HAS_RLIMITS, reason="memory limits need Linux/macOS (use Docker on Windows)")
def test_enforces_memory_limit():
    r = run_code("x = bytearray(2_000_000_000)", mem_mb=256)
    assert r["status"] == "error"
    assert "MemoryError" in r["stderr"]


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    r = run_code("import os; print(os.environ.get('ANTHROPIC_API_KEY'))")
    assert r["stdout"].strip() == "None"


def test_helper_modules_available_in_sandbox():
    r = run_code("import ta, perf, fa, ptc\nprint(fa.growth([1, 2]))")
    assert r["status"] == "ok", r
    assert r["stdout"].strip() == "[None, 1.0]"


def test_unicode_output():
    r = run_code("print('PKR ₨ and € ok')")
    assert "€" in r["stdout"]


def test_sandbox_service_runs_code_over_http(monkeypatch):
    """Production path: the worker sends code to the isolated sandbox service."""
    import threading

    from app.sandbox import runner, server
    srv = server.serve("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(runner.settings, "sandbox_url", f"http://127.0.0.1:{srv.server_address[1]}")
        r = runner.run_code("import perf\nprint(6*7)")
        assert r["status"] == "ok" and r["stdout"].strip() == "42"
        bad = runner.run_code("1/0")
        assert bad["status"] == "error" and "ZeroDivisionError" in bad["stderr"]
    finally:
        srv.shutdown()


def test_sandbox_service_down_gives_clear_error(monkeypatch):
    from app.sandbox import runner
    monkeypatch.setattr(runner.settings, "sandbox_url", "http://127.0.0.1:9")
    r = runner.run_code("print(1)")
    assert r["status"] == "error" and "unavailable" in r["stderr"]


def test_prod_compose_isolates_sandbox():
    import pytest
    yaml = pytest.importorskip("yaml")
    d = yaml.safe_load(open("deploy/docker-compose.prod.yml"))
    sb = d["services"]["sandbox"]
    assert sb["networks"] == ["sandbox_net"] and d["networks"]["sandbox_net"]["internal"] is True
    assert "env_file" not in sb and sb["read_only"] is True and sb["cap_drop"] == ["ALL"]
