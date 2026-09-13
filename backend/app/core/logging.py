"""Minimal structured logging plus the screening audit trail.

Audit entries are deliberately separate from application logs: doc §19 requires a
retained record of every screening decision and override.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from typing import Any

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return

    # Windows consoles default to cp1252, which raises UnicodeEncodeError on the
    # em-dashes and check marks that appear in audit lines and seed output. Force
    # UTF-8 rather than stripping characters from the messages.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def audit(event: str, **fields: Any) -> None:
    """One line per auditable decision, JSON so it can be shipped to a log sink."""
    logger = get_logger("audit")
    logger.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))
