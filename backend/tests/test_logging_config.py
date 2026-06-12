from __future__ import annotations

import logging
from configparser import ConfigParser
from logging.config import fileConfig
from pathlib import Path


BACKEND_ROOT = Path(__file__).parents[1]


def test_alembic_logging_preserves_application_loggers() -> None:
    env_text = BACKEND_ROOT.joinpath("alembic", "env.py").read_text()

    assert "disable_existing_loggers=False" in env_text


def test_alembic_root_logger_keeps_application_info_logs_visible() -> None:
    config = ConfigParser()
    config.read(BACKEND_ROOT.joinpath("alembic.ini"))

    assert config["logger_root"]["level"] == "INFO"


def test_alembic_file_config_does_not_disable_existing_application_logger() -> None:
    app_logger = logging.getLogger("ai_stock_sentinel.data_sources.finmind_client")
    app_logger.disabled = False

    fileConfig(BACKEND_ROOT.joinpath("alembic.ini"), disable_existing_loggers=False)

    assert app_logger.disabled is False
    assert logging.getLogger().getEffectiveLevel() == logging.INFO
