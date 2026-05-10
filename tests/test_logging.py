"""Tests for JSON logging configuration."""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, configure_logging


def test_json_formatter_emits_serializable_payload() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.custom_field = {"foo": "bar"}
    record.unserialisable = object()  # falls back to repr

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello world"
    assert payload["custom_field"] == {"foo": "bar"}
    assert isinstance(payload["unserialisable"], str)


def test_json_formatter_serialises_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=None,
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "exception" in payload
    assert "RuntimeError" in payload["exception"]


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")
    root = logging.getLogger()
    # Second call replaces the handler rather than stacking.
    assert len(root.handlers) == 1
    assert root.level == logging.INFO
