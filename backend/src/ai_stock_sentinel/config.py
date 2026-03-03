import os
from dataclasses import dataclass


@dataclass
class Settings:
    openai_api_key: str | None
    openai_model: str


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
