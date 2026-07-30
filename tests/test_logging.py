"""Tests for stock_mcp.server logging setup."""

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

from stock_mcp.server import _setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logging():
    """Snapshot and restore the root logger so tests don't pollute each other."""
    root = logging.getLogger()
    old_level = root.level
    old_handlers = root.handlers[:]
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    yield
    root.handlers[:] = old_handlers
    root.setLevel(old_level)


def _stderr_handler() -> logging.Handler | None:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr:
            return handler
    return None


def _file_handler() -> logging.Handler | None:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, (logging.handlers.RotatingFileHandler, logging.FileHandler)):
            return handler
    return None


def test_setup_logging_no_file_by_default():
    _setup_logging("INFO")
    assert _stderr_handler() is not None
    assert _file_handler() is None


def test_setup_logging_file_output(tmp_path):
    log_file = tmp_path / "app.log"
    _setup_logging("INFO", str(log_file))

    logger = logging.getLogger("stock_mcp.test")
    logger.info("hello file logger")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello file logger" in content
    assert "INFO" in content


def test_setup_logging_creates_directory(tmp_path):
    log_file = tmp_path / "logs" / "nested" / "app.log"
    _setup_logging("INFO", str(log_file))

    assert log_file.parent.exists()
    assert _file_handler() is not None


def test_setup_logging_uses_rotating_handler_when_max_bytes_positive(tmp_path):
    log_file = tmp_path / "app.log"
    _setup_logging("INFO", str(log_file), log_max_bytes=1024)

    handler = _file_handler()
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == 1024


def test_setup_logging_uses_plain_handler_when_max_bytes_zero(tmp_path):
    log_file = tmp_path / "app.log"
    _setup_logging("INFO", str(log_file), log_max_bytes=0)

    handler = _file_handler()
    assert type(handler) is logging.FileHandler  # noqa: E721


def test_setup_logging_keeps_stderr_when_file_configured(tmp_path):
    log_file = tmp_path / "app.log"
    _setup_logging("INFO", str(log_file))

    assert _stderr_handler() is not None
    assert _file_handler() is not None


def test_setup_logging_no_duplicate_stderr_handlers():
    _setup_logging("INFO")
    _setup_logging("INFO")

    root = logging.getLogger()
    stderr_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
    ]
    assert len(stderr_handlers) == 1


def test_setup_logging_bad_directory_is_graceful(tmp_path, caplog):
    # Use a path whose parent cannot be created (file where a dir should be).
    blocking_file = tmp_path / "blocked"
    blocking_file.write_text("not a directory", encoding="utf-8")
    log_file = blocking_file / "app.log"

    with caplog.at_level("WARNING", logger="stock_mcp"):
        _setup_logging("INFO", str(log_file))

    assert _file_handler() is None
    assert "Could not create log directory" in caplog.text


def test_setup_logging_file_format_includes_lineno(tmp_path):
    log_file = tmp_path / "app.log"
    _setup_logging("INFO", str(log_file))

    logger = logging.getLogger("stock_mcp.test")
    logger.info("line number test")

    content = log_file.read_text(encoding="utf-8")
    # The file format includes [name:lineno].
    assert "stock_mcp.test:" in content
