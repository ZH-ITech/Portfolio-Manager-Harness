"""GitHub -> Jira sync (scripts/jira_sync.py) against a fake Jira API."""
import json

import httpx

from scripts import jira_sync as js


def _event(action, merged=False, branch="PMH-16-sandbox", title="PMH-16 isolate sandbox", body=""):
    return {"action": action, "repository": {"full_name": "ZH-ITech/Portfolio-Manager-Harness"},
            "pull_request": {"number": 12, "html_url": "https://github.com/x/y/pull/12", "title": title, "body": body,
                             "merged": merged, "head": {"ref": branch}, "base": {"ref": "main"}, "user": {"login": "ZH-ITech"}}}


class FakeJira:
    def __init__(self, status="To Do"):
        self.status, self.calls = status, []

    def handler(self, req: httpx.Request):
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, body))
        if req.method == "GET" and path.endswith("/transitions"):
            return httpx.Response(200, json={"transitions": [{"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
                                                             {"id": "31", "name": "Done", "to": {"name": "Done"}}]})
        if req.method == "GET":
            return httpx.Response(200, json={"fields": {"status": {"name": self.status}}})
        if path.endswith("/transitions"):
            self.status = {"21": "In Progress", "31": "Done"}[body["transition"]["id"]]
            return httpx.Response(204)
        return httpx.Response(201, json={})


def _jira(fake):
    return js.Jira("https://example.atlassian.net", "e", "t", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_keys_found_in_branch_title_and_body_without_duplicates():
    assert js.issue_keys("PMH-16-sandbox", "PMH-16 and PMH-10", "see ABC-1") == ["PMH-16", "PMH-10"]
    assert js.closing_keys("Closes PMH-16, fixes pmh-10") == {"PMH-16", "PMH-10"}


def test_opened_pr_links_comments_and_moves_to_in_progress():
    fake = FakeJira("To Do")
    log = js.run(_event("opened"), _jira(fake))
    assert fake.status == "In Progress"
    link = next(b for m, p, b in fake.calls if p.endswith("/remotelink"))
    assert link["globalId"] == "github-pr:ZH-ITech/Portfolio-Manager-Harness#12"   # same id = update, not duplicate
    assert any("commented" in line for line in log)


def test_opened_pr_does_not_move_a_done_issue_backwards():
    fake = FakeJira("Done")
    js.run(_event("opened"), _jira(fake))
    assert fake.status == "Done"


def test_merge_moves_to_done_only_with_closes_keyword():
    fake = FakeJira("In Progress")
    js.run(_event("closed", merged=True), _jira(fake))
    assert fake.status == "In Progress"          # merged, but not marked as closing
    js.run(_event("closed", merged=True, body="Closes PMH-16"), _jira(fake))
    assert fake.status == "Done"


def test_closed_without_merge_and_prs_without_keys_do_nothing():
    assert js.plan(_event("closed", merged=False)) == []
    assert js.plan(_event("opened", branch="feature/x", title="tidy up")) == []


def test_one_failing_issue_does_not_stop_the_rest():
    def handler(req):
        if "PMH-99" in req.url.path:
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        return FakeJira().handler(req)
    jira = js.Jira("https://e.atlassian.net", "e", "t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    log = js.run(_event("opened", title="PMH-99 and PMH-16"), jira)
    assert any("PMH-99" in line and "failed" in line for line in log)
    assert any(line.startswith("PMH-16") and "failed" not in line for line in log)


def test_missing_secrets_skip_cleanly(monkeypatch, capsys):
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert js.main() == 0 and "skipping" in capsys.readouterr().out
