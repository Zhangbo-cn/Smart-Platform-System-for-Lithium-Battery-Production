from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

_INSECURE_JWT_SECRETS = frozenset({"change-me-in-prod!!", "change-me-in-prod"})
_INSECURE_SERVICE_KEYS = frozenset({"dev-router-key", ""})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    anthropic_api_key: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_primary_model: str = "deepseek-v4-flash"
    llm_flash_model: str = "deepseek-chat"  # 简单任务用更便宜的模型
    llm_fallback_model: str = "qwen2.5-72b-instruct"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    local_llm_base_url: str | None = None  # 本地 vLLM 地址，如 http://gpu-node:8000/v1
    local_llm_api_key: str | None = None   # 本地 vLLM 通常为 "EMPTY"
    local_llm_model: str = "qwen2.5-72b-instruct"  # 本地部署的模型名
    # 数据安全：HIGH 敏感度数据无 local 时直接拒绝（不降级 external）
    reject_external_for_high_sensitivity: bool = True

    redis_url: str = "redis://localhost:6379/0"
    short_term_ttl_seconds: int = 1800
    postgres_dsn: str = "postgresql+asyncpg://battery:battery@localhost:5432/battery_agent"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "battery_cases"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    mcp_mes_url: str = "http://localhost:8101/sse"
    mcp_scada_url: str = "http://localhost:8102/sse"
    mcp_erp_url: str = "http://localhost:8103/sse"
    mcp_lims_url: str = "http://localhost:8104/sse"

    jwt_secret_key: str = Field(default="change-me-in-prod!!", min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    otel_exporter_otlp_endpoint: str | None = None
    langsmith_api_key: str | None = None
    langsmith_project: str = "battery-agent"

    hitl_confidence_threshold: float = 0.7
    max_reflection_loops: int = 3
    api_port: int = 8003
    internal_service_key: str | None = Field(default="dev-router-key")
    langgraph_checkpoint_backend: Literal["memory", "redis"] = "memory"
    registry_url: str = "http://localhost:8021"

    @model_validator(mode="after")
    def _reject_insecure_prod_secrets(self) -> Self:
        if self.app_env.lower() not in ("prod", "production"):
            return self
        if self.jwt_secret_key in _INSECURE_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY must be set to a strong value when APP_ENV=prod")
        if not self.internal_service_key or self.internal_service_key in _INSECURE_SERVICE_KEYS:
            raise ValueError("INTERNAL_SERVICE_KEY must be set when APP_ENV=prod")
        if self.langgraph_checkpoint_backend == "memory":
            raise ValueError(
                "LANGGRAPH_CHECKPOINT_BACKEND=redis required when APP_ENV=prod "
                "(memory checkpointer loses HITL state on restart)"
            )
        return self

    def resolved_llm_api_key(self) -> str:
        key = self.llm_api_key or self.anthropic_api_key
        if not key:
            raise ValueError("Set LLM_API_KEY or ANTHROPIC_API_KEY in .env")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
