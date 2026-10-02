"""Runtime configuration loaded from environment variables / a local .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Maps Settings field -> environment variable name.
_ENV_MAP: dict[str, str] = {
    "youtube_api_key": "YOUTUBE_API_KEY",
    "yt_regions": "YT_REGIONS",
    "yt_category_id": "YT_CATEGORY_ID",
    "yt_max_results_per_region": "YT_MAX_RESULTS_PER_REGION",
    "yt_outlier_ratio": "YT_OUTLIER_RATIO",
    "top_topics": "TOP_TOPICS",
    "request_timeout": "REQUEST_TIMEOUT",
    "output_dir": "OUTPUT_DIR",
}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    youtube_api_key: SecretStr

    yt_regions: list[str] = Field(default_factory=lambda: ["US"])
    # 20 = Gaming, 28 = Science & Technology; None (env "all") = every category.
    yt_category_id: str | None = "20"
    # The mostPopular chart is capped at 200 results per region by YouTube.
    yt_max_results_per_region: int = Field(50, ge=1, le=200)
    # A video is an outlier when views >= ratio * channel subscribers.
    yt_outlier_ratio: float = Field(2.0, gt=0)

    top_topics: int = Field(10, ge=1, le=100)
    request_timeout: float = Field(15.0, gt=0)
    output_dir: Path = PROJECT_ROOT / "output"

    @field_validator("yt_regions", mode="before")
    @classmethod
    def _parse_regions(cls, value: object) -> object:
        if isinstance(value, str):
            value = [part for part in value.split(",")]
        if isinstance(value, list):
            regions = [str(r).strip().upper() for r in value if str(r).strip()]
            invalid = [r for r in regions if len(r) != 2 or not r.isalpha()]
            if invalid:
                raise ValueError(f"Region codes must be ISO 3166-1 alpha-2, got {invalid}")
            if not regions:
                raise ValueError("At least one region is required")
            return regions
        return value

    @field_validator("yt_category_id", mode="before")
    @classmethod
    def _parse_category(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"", "all", "0"}:
            return None
        if not text.isdigit():
            raise ValueError(f"Category must be a numeric YouTube category id or 'all', got {value!r}")
        return text

    @field_validator("output_dir", mode="after")
    @classmethod
    def _resolve_output_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    def with_overrides(self, **overrides: object) -> Settings:
        """Validated copy with the given fields replaced (None values are ignored).

        Use the sentinel "all" (not None) to clear the category filter.
        """
        updates = {k: v for k, v in overrides.items() if v is not None}
        if not updates:
            return self
        return Settings.model_validate({**self.model_dump(), **updates})


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for field, env_name in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is not None and raw.strip() != "":
            values[field] = raw.strip()
    return values


@lru_cache(maxsize=1)
def get_settings(env_file: str | None = None) -> Settings:
    """Load settings once per process. Real environment variables win over .env values."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    try:
        return Settings(**_read_env())
    except ValidationError as exc:
        problems = "; ".join(
            f"{_ENV_MAP.get(str(err['loc'][0]), err['loc'][0])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration ({problems}). See .env.example.") from exc
