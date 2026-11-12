"""Keep Jira in sync with GitHub pull requests (Jira PMH-20).

Run by .github/workflows/jira-sync.yml on pull_request events. For every Jira key
(e.g. PMH-16) found in the branch name, PR title or PR body:
  * opened / reopened / ready_for_review: add or update a web link to the PR,
    comment, and move the issue from To Do to In Progress;
  * closed and merged: comment that it was merged; move to Done only when the PR
    title or body says "Closes PMH-16" (also Fixes / Resolves).
Needs repository secrets JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN; without them it
prints a notice and exits cleanly."""
import json
import os
import re
import sys

import httpx

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
CLOSE_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+([A-Z][A-Z0-9]+-\d+)\b", re.I)
PROJECT = os.environ.get("JIRA_PROJECT_KEY", "PMH")


def issue_keys(*texts: str) -> list[str]:
    keys = []
    for t in texts:
        for k in KEY_RE.findall(t or ""):
            if k.startswith(PROJECT + "-") and k not in keys:
                keys.append(k)
    return keys


def closing_keys(*texts: str) -> set[str]:
    return {k.upper() for t in texts for k in CLOSE_RE.findall(t or "") if k.upper().startswith(PROJECT + "-")}


def plan(event: dict) -> list[dict]:
    """Turn a GitHub pull_request event into Jira actions (pure function, easy to test)."""
    action = event.get("action")
    pr = event.get("pull_request") or {}
    title, body = pr.get("title") or "", pr.get("body") or ""
    branch = (pr.get("head") or {}).get("ref") or ""
    keys = issue_keys(branch, title, body)
    if not keys:
        return []
    repo = (event.get("repository") or {}).get("full_name", "")
    number, url, user = pr.get("number"), pr.get("html_url"), (pr.get("user") or {}).get("login", "")
    link = {"globalId": f"github-pr:{repo}#{number}", "url": url, "title": f"PR #{number}: {title}"}
    steps = []
    if action in ("opened", "reopened", "ready_for_review"):
        for k in keys:
            steps += [{"op": "link", "key": k, **link},
                      {"op": "comment", "key": k, "text": f"Pull request #{number} opened by {user}: {title} ({url})"},
                      {"op": "transition", "key": k, "to": "In Progress", "only_from": ["To Do"]}]
    elif action == "closed" and pr.get("merged"):
        closes = closing_keys(title, body)
        for k in keys:
            steps += [{"op": "link", "key": k, **link},
                      {"op": "comment", "key": k, "text": f"Pull request #{number} merged into {(pr.get('base') or {}).get('ref', 'main')}: {title} ({url})"}]
            if k in closes:
                steps.append({"op": "transition", "key": k, "to": "Done", "only_from": None})
    return steps


class Jira:
    def __init__(self, base: str, email: str, token: str, client: httpx.Client | None = None):
        self.base = base.rstrip("/")
        self.http = client or httpx.Client(auth=(email, token), timeout=30,
                                           headers={"Accept": "application/json"})

    def _u(self, path):
        return f"{self.base}/rest/api/3/{path}"

    def link(self, key, globalId, url, title):
        r = self.http.post(self._u(f"issue/{key}/remotelink"),
                           json={"globalId": globalId, "object": {"url": url, "title": title[:250]}})
        r.raise_for_status()

    def comment(self, key, text):
        doc = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
        self.http.post(self._u(f"issue/{key}/comment"), json={"body": doc}).raise_for_status()

    def transition(self, key, to, only_from=None):
        if only_from:
            r = self.http.get(self._u(f"issue/{key}"), params={"fields": "status"})
            r.raise_for_status()
            status = r.json()["fields"]["status"]["name"]
            if status not in only_from:
                return f"{key}: stays in {status}"
        r = self.http.get(self._u(f"issue/{key}/transitions"))
        r.raise_for_status()
        ts = r.json().get("transitions", [])
        match = next((t for t in ts if t["name"].lower() == to.lower() or t["to"]["name"].lower() == to.lower()), None)
        if not match:
            return f"{key}: no transition to {to}"
        self.http.post(self._u(f"issue/{key}/transitions"), json={"transition": {"id": match["id"]}}).raise_for_status()
        return f"{key}: moved to {to}"


def run(event: dict, jira: Jira) -> list[str]:
    log = []
    for s in plan(event):
        try:
            if s["op"] == "link":
                jira.link(s["key"], s["globalId"], s["url"], s["title"])
                log.append(f"{s['key']}: linked {s['url']}")
            elif s["op"] == "comment":
                jira.comment(s["key"], s["text"])
                log.append(f"{s['key']}: commented")
            else:
                log.append(jira.transition(s["key"], s["to"], s["only_from"]))
        except (httpx.HTTPError, KeyError, ValueError) as e:   # one bad key must not stop the others
            log.append(f"{s['key']}: {s['op']} failed ({e})")
    return log


def main() -> int:
    base, email, token = (os.environ.get(k, "") for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
    if not (base and email and token):
        print("Jira secrets not set (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN); skipping.")
        return 0
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    for line in run(event, Jira(base, email, token)) or ["No PMH keys found in branch, title or body."]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
