"""Persistence: conversations, messages, runs (with progress), the step-by-step
audit log, and recorded decisions. SQLite for development; swap for Postgres
in production (same SQL)."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, ts REAL,
    role TEXT, content TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created REAL, status TEXT, request TEXT, report TEXT,
    error TEXT, conversation_id TEXT, stage TEXT, progress REAL DEFAULT 0, attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, agent TEXT,
    kind TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, run_id TEXT, created REAL, status TEXT,
    proposal TEXT, decided_by TEXT, decided_at REAL);
"""
RUN_COLUMNS = {"conversation_id": "TEXT", "stage": "TEXT", "progress": "REAL DEFAULT 0", "attempts": "INTEGER DEFAULT 0"}


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    c = sqlite3.connect(settings.db_path, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
        for col, typ in RUN_COLUMNS.items():   # migrate databases created by the first scaffold
            if col not in have:
                c.execute(f"ALTER TABLE runs ADD COLUMN {col} {typ}")


# ---------- conversations ----------
def create_conversation(title: str) -> str:
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO conversations VALUES (?,?,?,?)", (cid, now, now, title[:80]))
    return cid


def conversation_exists(cid: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone() is not None


def add_message(cid: str, role: str, content, run_id: str | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT INTO messages (conversation_id, ts, role, content, run_id) VALUES (?,?,?,?,?)",
                  (cid, time.time(), role, json.dumps(content), run_id))
        c.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))


def get_messages(cid: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT ts, role, content, run_id FROM messages WHERE conversation_id=? ORDER BY id",
                         (cid,)).fetchall()
    return [{"ts": r["ts"], "role": r["role"], "content": json.loads(r["content"]), "run_id": r["run_id"]} for r in rows]


def list_conversations(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM conversations ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- runs ----------
def create_run(request: dict, conversation_id: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute("INSERT INTO runs (id, created, status, request, conversation_id, stage, progress) VALUES (?,?,?,?,?,?,?)",
                  (run_id, time.time(), "queued", json.dumps(request), conversation_id, "Queued", 0))
    return run_id


def set_status(run_id: str, status: str, report: dict | None = None, error: str | None = None) -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET status=?, report=COALESCE(?, report), error=? WHERE id=?",
                  (status, json.dumps(report) if report else None, error, run_id))


def set_progress(run_id: str, stage: str, progress: float) -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET stage=?, progress=? WHERE id=?", (stage[:200], max(0.0, min(1.0, progress)), run_id))


def bump_attempt(run_id: str) -> int:
    with _conn() as c:
        c.execute("UPDATE runs SET attempts=attempts+1 WHERE id=?", (run_id,))
        return c.execute("SELECT attempts FROM runs WHERE id=?", (run_id,)).fetchone()["attempts"]


def log(run_id: str, agent: str, kind: str, payload: dict) -> None:
    with _conn() as c:
        c.execute("INSERT INTO events (run_id, ts, agent, kind, payload) VALUES (?,?,?,?,?)",
                  (run_id, time.time(), agent, kind, json.dumps(payload, default=str)))


def get_run(run_id: str, with_events: bool = True) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        evs = c.execute("SELECT ts, agent, kind, payload FROM events WHERE run_id=? ORDER BY id",
                        (run_id,)).fetchall() if with_events else []
    return {
        "id": r["id"], "status": r["status"], "created": r["created"], "stage": r["stage"],
        "progress": r["progress"], "attempts": r["attempts"], "conversation_id": r["conversation_id"],
        "request": json.loads(r["request"]),
        "report": json.loads(r["report"]) if r["report"] else None, "error": r["error"],
        "events": [{"ts": e["ts"], "agent": e["agent"], "kind": e["kind"], "payload": json.loads(e["payload"])}
                   for e in evs],
    }


# ---------- recorded decisions (human sign-off records; final once made) ----------
def create_approval(run_id: str, proposal: dict) -> str:
    aid = uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)",
                  (aid, run_id, time.time(), "pending", json.dumps(proposal), None, None))
    return aid


def list_approvals(status: str | None = None) -> list[dict]:
    with _conn() as c:
        q = "SELECT * FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY created DESC"
        rows = c.execute(q, (status,) if status else ()).fetchall()
    return [{**dict(r), "proposal": json.loads(r["proposal"])} for r in rows]


def decide_approval(aid: str, approve: bool, who: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                        ("approved" if approve else "rejected", who, time.time(), aid))
        return cur.rowcount == 1
