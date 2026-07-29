"""Command-line entry point for LecturaBot."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .bot import LecturaBot
from .config import ConfigError, load_config


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LECTURABOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    configure_logging()
    config_path = Path(os.environ.get("LECTURABOT_CONFIG", "config.toml"))
    try:
        config = load_config(config_path)
    except ConfigError as error:
        raise SystemExit(f"configuration error: {error}") from error

    bot = LecturaBot(config)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
