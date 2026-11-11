from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider:
    #   "openai"    = any OpenAI-compatible API. Default: Groq free tier running the
    #                 open-source Llama 3.3 70B. Also Ollama (local, offline) or OpenRouter.
    #   "anthropic" = Claude API (paid).
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_timeout_s: float = 120          # raise to ~300 for local CPU models (Ollama)
    llm_max_retries: int = 3            # in-request retries on rate limits (free tiers)
    anthropic_api_key: str = ""
    main_model: str = "llama-3.3-70b-versatile"
    subagent_model: str = "llama-3.3-70b-versatile"
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = True
    db_path: str = "data/harness.db"
    sandbox_timeout_s: int = 20
    sandbox_memory_mb: int = 512
    ptc_proxy_url: str = "http://127.0.0.1:8000/internal/tools"
    ptc_shared_secret: str = "change-me"
    max_agent_steps: int = 15
    max_tool_calls: int = 30          # per answer (permissions / cost limit)
    max_sandbox_runs: int = 8         # per answer
    history_turns: int = 10           # previous user/assistant turns sent to the model
    compaction_char_budget: int = 60_000   # rough proxy for context size
    tool_result_char_limit: int = 6_000    # what the model sees; full result goes to audit log


settings = Settings()
