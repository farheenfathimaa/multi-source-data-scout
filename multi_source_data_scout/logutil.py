"""Logging setup with clear, stage-aware console output."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
TIME_FORMAT = "%H:%M:%S"


def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("scout")
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, TIME_FORMAT))
        root.addHandler(handler)
    root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"scout.{name}")


def remove_handlers() -> None:
    root = logging.getLogger("scout")
    for h in list(root.handlers):
        root.removeHandler(h)