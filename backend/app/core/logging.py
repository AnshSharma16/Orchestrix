"""Logging setup for Orchestrix.

Standard library only. Nothing is configured at import time, and this module
never imports app.core.config: callers pass level and format explicitly.
"""

import json
import logging
import re
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

__all__ = ["configure_logging", "get_logger", "log_context"]

_HANDLER_MARK = "_orchestrix_handler"
_CTX_ATTR = "orchestrix_context"
_REDACTED = "[REDACTED]"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_FORMATS = ("console", "json")

# Loggers that would otherwise bypass our handler or print secret-bearing URLs.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_QUIET_LOGGERS = ("httpx", "httpcore")  # INFO lines include full request URLs

_SENSITIVE_KEY_PARTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "cookie", "credential", "database_url",
)
# user:password@ inside URLs, in message text and tracebacks.
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@\"'\\]+):[^@\s/\"'\\]+@")

# Attributes every LogRecord has; anything else came from `extra=`.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName", _CTX_ATTR}

_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "orchestrix_log_context", default=None
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (e.g. execution_id, job_id) to every log record emitted
    inside the block. Task-local, so safe across concurrent asyncio tasks."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def configure_logging(level: str | int = "INFO", log_format: str = "console") -> None:
    """Install the root handler. Safe to call repeatedly: only the handler
    created here is replaced, so there are never duplicates.

    Handlers installed by others (e.g. logging.basicConfig) are left alone,
    so remove basicConfig calls from application code.
    """
    numeric_level = _parse_level(level)
    if log_format not in _FORMATS:
        raise ValueError(f"log_format must be one of {_FORMATS}, got {log_format!r}")

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
            existing.close()

    handler = _StdoutHandler()
    handler.setFormatter(_JsonFormatter() if log_format == "json" else _ConsoleFormatter())
    handler.addFilter(_ContextFilter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Route uvicorn through the root handler and let LOG_LEVEL govern it.
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(logging.NOTSET)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(numeric_level, logging.WARNING))


def _parse_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    name = str(level).strip().upper()
    if name not in _LEVELS:
        raise ValueError(f"Unknown log level {level!r}; expected one of {list(_LEVELS)}")
    return _LEVELS[name]


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is *now* (survives pytest/redirect swaps).

    StreamHandler.__init__ assigns self.stream, which would fail against the
    read-only property below, so only Handler.__init__ runs. emit() and flush()
    are inherited and rely on `stream` and `terminator`; both are provided here
    explicitly rather than depending on class-level defaults.
    """

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        self.terminator = "\n"

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, _CTX_ATTR, _context.get() or {})
        return True


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Bound context plus `extra=` fields, with sensitive keys masked."""
    fields: dict[str, Any] = dict(getattr(record, _CTX_ATTR, None) or {})
    for key, value in record.__dict__.items():
        if key not in _RESERVED:
            fields[key] = value
    return {k: (_REDACTED if _is_sensitive(k) else v) for k, v in fields.items()}


def _scrub(text: str) -> str:
    return _URL_CREDENTIALS.sub(r"\1:***@", text)


class _ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        self.converter = time.gmtime  # UTC

    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record)
        fields = _extra_fields(record)
        if fields:
            line += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        return line

    def format(self, record: logging.LogRecord) -> str:
        return _scrub(super().format(record))


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in _extra_fields(record).items():
            payload.setdefault(key, value)  # core keys always win

        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, _ = record.exc_info
            payload["exception"] = {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "traceback": self.formatException(record.exc_info),
            }
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return _scrub(json.dumps(payload, default=str, ensure_ascii=False))