# Portfolio Manager Harness: AI Stock Assistant

An AI stock market assistant that brings **market data, company research and analysis together in one
conversational interface**. Ask in plain language; get an explanation with interactive charts or tables,
the sources used (with timestamps) and the assumptions made.

> Analysis only, not financial advice. The assistant is read-only: it never trades.

## What you can ask
| Feature | Example question |
|---|---|
| Technical analysis | "Is AAPL in an uptrend? Show the indicators." |
| Fundamental analysis | "How is MSFT valued compared with its growth?" |
| Comparison | "Compare NVDA, AMD and INTC over the last year." |
| What-if / projection | "What if I had invested $1,000 in TSLA two years ago?" |

Follow-up questions keep context ("now add GOOGL").

## How it works
```
Chat UI ──▶ FastAPI /api/chat ──▶ Celery task (Redis) ──▶ Harness (Claude Sonnet)
   ▲  progress, saved results          │ retries            ├─ permissions: allow-lists, limits
   │                                   ▼                    ├─ MCP tools: prices, profile, statements,
   └──── /api/market/chart|compare   audit log + history    │  earnings, news, technical summary
                                                            ├─ Python sandbox (ta, perf, fa, PTC)
                                                            └─ sub-agents (Claude Haiku)
```
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Path | Purpose |
|---|---|
| `app/harness/agent.py` | Conversational agent loop, tools, permissions budget, progress |
| `app/harness/context.py` | Result clipping and history compaction |
| `app/mcp_servers/market_data.py` | MCP server: market data and company information |
| `app/data/market.py` | Data provider (yfinance for NASDAQ; PSX stub), caching, errors |
| `app/analysis/` | Technical indicators, performance/what-if, fundamental ratios |
| `app/sandbox/` | Isolated code runner and the PTC client injected into it |
| `app/worker.py` | Celery task: progress, retries, saved results |
| `app/main.py` | API: chat, runs, conversations, chart data, PTC proxy |
| `static/index.html` | Chat UI with charts, tables, sources, history |

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt numpy
cp .env.example .env      # add ANTHROPIC_API_KEY; CELERY_EAGER=true runs jobs without Redis
uvicorn app.main:app --reload
# open http://localhost:8000
```
With Docker (API + Celery worker + Redis): `docker compose up --build`

## Checks
```bash
ruff check app tests
pytest -q
```
Tests cover indicators against reference definitions, performance and fundamentals maths, the agent
loop (self-debugging, blocked tools, usage limits), conversation context, the chat API (saved results,
failures, rate limit), chart endpoints, outage handling, sandbox limits and PTC tokens.

## Deploy
One EC2 instance (or any VM) running `docker compose`. Set `ANTHROPIC_API_KEY`, `PTC_SHARED_SECRET`
and `CELERY_EAGER=false` as secrets on the host, never in Git. See Jira PMH-19.

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md). Branch per Jira issue (`PMH-14-...`), key in every commit,
pull request with review, CI must pass.
