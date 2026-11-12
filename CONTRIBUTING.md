# Contributing

Work is tracked in Jira project **PMH**. Every change goes through a pull request.

1. Pick or get assigned a Jira issue and move it to **In Progress**.
2. Create a branch named after it: `git checkout -b PMH-14-market-data`.
3. Commit with the key first: `git commit -m "PMH-14 add caching to price history"`.
4. Run the checks locally: `ruff check app tests` and `pytest -q`.
5. Push and open a pull request using the template. CI must pass and one teammate reviews.
6. Merge. Jira updates itself (workflow `Jira sync`): opening a PR links it on the issue and moves
   it to In Progress; merging adds a comment, and moves it to **Done** if the PR says `Closes PMH-xx`.

Owners by area (see Jira for details): harness and agent (Zohaib), data/MCP and analysis (Waqas),
research, report and presentation (Areesha), sandbox and permissions (Shabir),
Celery, CI and deployment (Tahir), design and chat UI (Iqra).
