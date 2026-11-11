from app.sandbox.runner import run_code


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


def test_enforces_memory_limit():
    r = run_code("x = bytearray(2_000_000_000)", mem_mb=256)
    assert r["status"] == "error"
    assert "MemoryError" in r["stderr"]


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    r = run_code("import os; print(os.environ.get('ANTHROPIC_API_KEY'))")
    assert r["stdout"].strip() == "None"
