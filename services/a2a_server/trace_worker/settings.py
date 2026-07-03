from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TraceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mcp_mes_url: str = "http://localhost:8101/sse"
    mcp_scada_url: str = "http://localhost:8102/sse"
    registry_url: str = "http://localhost:8021"


@lru_cache
def get_settings() -> TraceSettings:
    return TraceSettings()
