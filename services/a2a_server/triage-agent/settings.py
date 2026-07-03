from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TriageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"


@lru_cache
def get_settings() -> TriageSettings:
    return TriageSettings()
