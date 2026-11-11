"""Celery background processing (PMH-18): every chat message runs as a task
with progress tracking, automatic retries for transient failures, and saved
results (the answer is stored in the run and in the conversation history)."""
import httpx
from celery import Celery

from app.config import settings
from app.data.market import DataSourceError
from app.harness import audit
from app.harness.llm import LLMTransientError

celery_app = Celery("pmh", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_always_eager=settings.celery_eager, task_acks_late=True,
                       worker_prefetch_multiplier=1, task_time_limit=900, task_soft_time_limit=840,
                       # keep real stdout/stderr so subprocesses (MCP servers) can inherit them
                       worker_redirect_stdouts=False)

# Errors worth retrying: LLM rate limits/outages, data-source outages, network problems
_transient = [LLMTransientError, DataSourceError, httpx.TransportError, ConnectionError, TimeoutError]
try:  # only relevant when LLM_PROVIDER=anthropic
    import anthropic
    _transient += [anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError]
except ImportError:
    pass
TRANSIENT = tuple(_transient)


@celery_app.task(bind=True, name="pmh.chat", max_retries=3)
def run_chat_task(self, run_id: str, conversation_id: str, message: str) -> str:
    from app.harness.agent import run_chat
    audit.init_db()
    attempt = audit.bump_attempt(run_id)
    audit.set_status(run_id, "running")
    progress = lambda stage, p: audit.set_progress(run_id, stage, p)  # noqa: E731
    try:
        answer = run_chat(run_id, conversation_id, message, progress)
        audit.set_progress(run_id, "Done", 1.0)
        audit.set_status(run_id, "done", report=answer)
        audit.add_message(conversation_id, "assistant", answer, run_id)
    except TRANSIENT as e:
        if attempt <= self.max_retries and not settings.celery_eager:
            delay = 5 * 2 ** (attempt - 1)
            audit.log(run_id, "worker", "retry", {"attempt": attempt, "in_seconds": delay, "error": repr(e)})
            audit.set_progress(run_id, f"Temporary error, retrying in {delay}s (attempt {attempt + 1})", 0.05)
            raise self.retry(exc=e, countdown=delay)
        _fail(run_id, conversation_id, e)
    except Exception as e:
        _fail(run_id, conversation_id, e)
    return run_id


def _fail(run_id, conversation_id, e):
    audit.log(run_id, "worker", "run_error", {"error": repr(e)})
    audit.set_status(run_id, "error", error=f"{type(e).__name__}: {e}")
    audit.set_progress(run_id, "Failed", 1.0)
    audit.add_message(conversation_id, "error", {"text": f"Sorry, this analysis failed: {type(e).__name__}: {e}"}, run_id)
