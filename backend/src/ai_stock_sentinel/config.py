import logging as _logging
import os
from dataclasses import dataclass

# 2.0.0 is the first deterministic contract with no news/LLM-derived decisions.
STRATEGY_VERSION = "2.0.0"


@dataclass(slots=True, frozen=True)
class Settings:
    daily_radar_internal_token: str | None


def load_settings() -> Settings:
    return Settings(
        daily_radar_internal_token=os.getenv("DAILY_RADAR_INTERNAL_TOKEN"),
    )


def configure_logging(level: int = _logging.INFO) -> None:
    """設定 root logger。應在應用程式入口呼叫一次。"""
    _logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
