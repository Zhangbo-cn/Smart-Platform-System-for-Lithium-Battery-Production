from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Report8dSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mcp_qms_url: str = "http://localhost:8105/sse"
    reporter_mode: Literal["deep_agent", "template"] = "deep_agent"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    langsmith_api_key: str = ""
    langsmith_project: str = "battery-reporter-agent"
    registry_url: str = "http://localhost:8021"
    http_timeout: float = 120.0


@lru_cache
def get_settings() -> Report8dSettings:
    return Report8dSettings()
