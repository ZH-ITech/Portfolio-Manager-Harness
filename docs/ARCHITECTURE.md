# Architecture

## Request flow
1. The user sends a message in the chat UI (`POST /api/chat`). The API stores it, creates a run and
   queues a Celery task. Every message runs in the background, so long analyses never block the UI.
2. The worker loads the last turns of the conversation and starts the harness. Progress stages
   ("Fetching price history", "Running analysis code", ...) are written to the run; the UI polls them.
3. The orchestrator (Claude Sonnet) chooses tools: MCP data tools for facts, `run_python` for
   calculations, `delegate_research` for narrow tasks given to a sub-agent (Claude Haiku).
4. It finishes with a structured `answer`: text, chart requests, tables, sources, assumptions.
   Chart requests name a ticker/period/indicators; the UI fetches the data from `/api/market/chart`
   or `/api/market/compare`, so large datasets never pass through the model.
5. The answer is saved to the run and the conversation (re-openable from history).

## Harness responsibilities
| Concern | Implementation |
|---|---|
| Model | `MAIN_MODEL` (orchestrator), `SUBAGENT_MODEL` (sub-agents, compaction) |
| Context | last `HISTORY_TURNS` turns; large tool results clipped; history compacted when over budget |
| Permissions | per-agent tool allow-list; read-only tools only; `MAX_TOOL_CALLS`, `MAX_SANDBOX_RUNS` per answer; API rate limit |
| Workflow | tool-use loop with errors returned to the model for self-correction; forced answer at the step limit |
| Observability | every model step, tool call, data timestamp, sandbox run and error in the audit log |

## Sandbox and PTC
Generated code runs in a separate interpreter with CPU, memory, file-size, process and wall-clock
limits and a scrubbed environment. Each run gets `ptc.py` (`call_tool`, `prices`) plus `ta.py`,
`perf.py` and `fa.py` from `app/analysis/`. `call_tool` reaches registered MCP tools through
`/internal/tools/{name}`, which checks a per-run HMAC token and logs each call.

Next step (PMH-16): run the same `run_code()` interface in a container with network limited to the
PTC proxy.

## Background processing
`app/worker.py`: Redis broker, `acks_late`, time limits, automatic retries with exponential backoff for
transient API/network errors (attempt count shown in the UI), failures recorded with a reason.

## Data
`app/data/market.py`: yfinance for NASDAQ/NYSE (delayed, non-commercial), 5-minute cache, clear
errors for unknown tickers (404) and outages (503). PSX provider is a stub (stretch goal).

## Open questions
PSX data source; final hosting details; confirmation of the PTC definition with the instructor.
