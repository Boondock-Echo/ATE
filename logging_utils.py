"""Shared logging helpers for ATE scripts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from path_utils import ensure_directory, resolve_log_file


LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def resolve_log_path(
    app_name: str,
    log_file: Optional[str],
    default_name: str,
) -> Optional[Path]:
    """Resolve a CLI-provided log file path or standard log location."""

    if log_file is None:
        return None
    if log_file == "":
        return resolve_log_file(app_name, default_name)

    candidate = Path(log_file).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate
    return resolve_log_file(app_name, log_file)


def setup_logging(
    component: str,
    *,
    log_file: Optional[Path] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure a component logger with stdout and optional file handlers."""

    logger = logging.getLogger(component)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if log_file is not None:
        ensure_directory(log_file.parent)
        if not any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) == log_file
            for handler in logger.handlers
        ):
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger
