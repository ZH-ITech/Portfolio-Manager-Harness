from app.harness import audit
from app.harness.context import clip
from app.harness.security import ptc_token, verify_ptc_token


def test_ptc_token_roundtrip():
    assert verify_ptc_token(ptc_token("abc123")) == "abc123"
    assert verify_ptc_token("abc123.forged") is None


def test_approval_requires_human_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "t.db"))
    audit.init_db()
    rid = audit.create_run({"capital": 1})
    aid = audit.create_approval(rid, {"ticker": "AAPL", "action": "buy", "quantity": 1, "rationale": "x"})
    assert audit.list_approvals("pending")[0]["id"] == aid
    assert audit.decide_approval(aid, True, "reviewer")
    assert not audit.decide_approval(aid, False, "again")  # cannot be re-decided


def test_clip_truncates():
    assert "truncated" in clip("x" * 100, limit=10)
