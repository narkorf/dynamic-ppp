"""Application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"


class Settings(BaseSettings):
    """Environment-driven settings for the API and its local data sources."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Dynamic Purchasing Power Parity API"
    app_env: str = "production"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    geoip_db_path: Path = Field(
        default=DEFAULT_DATA_DIR / "ip-to-country.mmdb",
        validation_alias=AliasChoices("GEOIP_DB_PATH", "MAXMIND_DB_PATH"),
    )
    ppp_data_path: Path = Field(default=DEFAULT_DATA_DIR / "ppp_snapshot.json")
    ppp_max_discount: float = Field(default=0.80, gt=0.0, le=1.0)
    world_bank_indicator: str = "PA.NUS.PPPC.RF"
    iplocate_api_key: str | None = None
    iplocate_variant: str = "daily"
    iplocate_download_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_inputs(cls, data: Any) -> Any:
        """Accept legacy config keys while preferring provider-agnostic names."""

        if isinstance(data, dict) and "geoip_db_path" not in data and "maxmind_db_path" in data:
            data = dict(data)
            data["geoip_db_path"] = data["maxmind_db_path"]
        return data

    @property
    def maxmind_db_path(self) -> Path:
        """Backward-compatible alias for the GeoIP MMDB path."""

        return self.geoip_db_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for the running process."""

    return Settings()
