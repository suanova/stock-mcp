"""Tests for stock_mcp.config."""

import os

import pytest

from stock_mcp.config import Config, load_config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Start each test with a clean environment."""
    for name in (
        "DSA_API_BASE_URL",
        "DSA_API_AUTH_ENABLED",
        "DSA_API_PASSWORD",
        "DSA_MCP_HOST",
        "DSA_MCP_PORT",
        "DSA_MCP_PATH",
        "DSA_MCP_ALLOWED_HOSTS",
        "DSA_API_TIMEOUT",
        "DSA_API_REQUEST_TIMEOUT",
        "DSA_MCP_LOG_LEVEL",
        "DSA_MCP_LOG_FILE",
        "DSA_MCP_LOG_MAX_BYTES",
        "DSA_MCP_LOG_BACKUP_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_config_defaults():
    cfg = load_config()
    assert cfg.api_base_url == "http://127.0.0.1:8000"
    assert cfg.auth_enabled is False
    assert cfg.mcp_host == "127.0.0.1"
    assert cfg.mcp_port == 8765
    assert cfg.mcp_path == "/mcp"
    assert cfg.log_level == "INFO"
    assert cfg.log_file is None
    assert cfg.log_max_bytes == 10 * 1024 * 1024
    assert cfg.log_backup_count == 3


def test_load_config_log_env_vars(monkeypatch, tmp_path):
    log_file = tmp_path / "app.log"
    monkeypatch.setenv("DSA_MCP_LOG_FILE", str(log_file))
    monkeypatch.setenv("DSA_MCP_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("DSA_MCP_LOG_BACKUP_COUNT", "5")

    cfg = load_config()
    assert cfg.log_file == str(log_file)
    assert cfg.log_max_bytes == 1024
    assert cfg.log_backup_count == 5


def test_load_config_log_file_tilde_expansion(monkeypatch):
    monkeypatch.setenv("DSA_MCP_LOG_FILE", "~/stock-mcp.log")
    cfg = load_config()
    assert cfg.log_file == os.path.expanduser("~/stock-mcp.log")


def test_load_config_empty_log_file_is_none(monkeypatch):
    monkeypatch.setenv("DSA_MCP_LOG_FILE", "")
    cfg = load_config()
    assert cfg.log_file is None


def test_load_config_whitespace_log_file_is_none(monkeypatch):
    monkeypatch.setenv("DSA_MCP_LOG_FILE", "   ")
    cfg = load_config()
    assert cfg.log_file is None


def test_load_config_invalid_log_max_bytes():
    cfg = Config(
        api_base_url="http://127.0.0.1:8000",
        auth_enabled=False,
        auth_password="",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        mcp_path="/mcp",
        mcp_allowed_hosts=[],
        api_timeout=30.0,
        api_request_timeout=600.0,
        log_level="INFO",
        log_file=None,
        log_max_bytes=-1,
        log_backup_count=3,
    )
    with pytest.raises(ValueError, match="DSA_MCP_LOG_MAX_BYTES"):
        cfg.validate()


def test_load_config_invalid_log_backup_count():
    cfg = Config(
        api_base_url="http://127.0.0.1:8000",
        auth_enabled=False,
        auth_password="",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        mcp_path="/mcp",
        mcp_allowed_hosts=[],
        api_timeout=30.0,
        api_request_timeout=600.0,
        log_level="INFO",
        log_file=None,
        log_max_bytes=10 * 1024 * 1024,
        log_backup_count=-1,
    )
    with pytest.raises(ValueError, match="DSA_MCP_LOG_BACKUP_COUNT"):
        cfg.validate()
