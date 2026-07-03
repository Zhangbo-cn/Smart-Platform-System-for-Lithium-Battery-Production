from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class PlannerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    planner_mode: Literal["react", "rule"] = "react"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.1
    max_react_turns: int = 8
    http_timeout: float = 60.0
    registry_url: str = "http://localhost:8021"


@lru_cache
def get_settings() -> PlannerSettings:
    return PlannerSettings()
