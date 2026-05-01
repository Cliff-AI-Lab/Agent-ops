from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _default_env_file() -> str:
    """Select the environment file from HARNESS_ENV."""
    harness_env = os.environ.get("HARNESS_ENV", "dev").strip() or "dev"
    return f".env.{harness_env}"


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    sandbox_api_base: str = ""
    ruidong_api_key: str | None = None
    harness_env: str = "dev"
    harness_default_model: str = ""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Load the environment-specific dotenv file on each instantiation."""
        dynamic_dotenv = DotEnvSettingsSource(
            settings_cls,
            env_file=_default_env_file(),
            env_file_encoding="utf-8",
        )
        return init_settings, env_settings, dynamic_dotenv, file_secret_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings object."""
    return Settings()
