import logging
from typing import cast

import structlog

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.observability.redaction import redact_event_dict


def configure_logging(settings: AppSettings) -> None:
    logging.basicConfig(
        level=_stdlib_log_level(settings.log_level.value),
        format="%(message)s",
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_event_dict,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


def _stdlib_log_level(value: str) -> int:
    levels: dict[str, int] = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    return levels[value]
