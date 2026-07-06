"""Central logging configuration for Access Assistant."""

from __future__ import annotations

import logging
import os

DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] %(funcName)s:%(lineno)d | %(message)s"
)


def configure_logging(*, force: bool = False) -> None:
    """Apply root logging format/level from environment."""
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.getenv("LOG_FORMAT", DEFAULT_LOG_FORMAT).strip() or DEFAULT_LOG_FORMAT

    if logging.root.handlers:
        if not force:
            return
        for handler in logging.root.handlers:
            handler.setFormatter(logging.Formatter(fmt))
        logging.root.setLevel(level)
        return

    logging.basicConfig(level=level, format=fmt, force=True)
