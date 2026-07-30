"""Tests for built-in .env loading at startup."""

from unittest.mock import MagicMock, patch

from stock_mcp.config import Config
from stock_mcp.server import run


def test_run_loads_dotenv_before_loading_config():
    """run() must call load_dotenv() before load_config() so .env values are used."""
    config = Config(
        api_base_url="http://127.0.0.1:8000",
        auth_enabled=False,
        auth_password="",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        mcp_path="/mcp",
        mcp_allowed_hosts=[],
        mcp_stateless_http=False,
        api_timeout=30.0,
        api_request_timeout=600.0,
        log_level="INFO",
        log_file=None,
        log_max_bytes=10 * 1024 * 1024,
        log_backup_count=3,
    )

    call_order = []

    def fake_load_dotenv(*args, **kwargs):
        call_order.append("load_dotenv")
        return True

    def fake_load_config(*args, **kwargs):
        call_order.append("load_config")
        return config

    with (
        patch("stock_mcp.server.load_dotenv", side_effect=fake_load_dotenv),
        patch("stock_mcp.server.load_config", side_effect=fake_load_config),
        patch("stock_mcp.server._setup_logging"),
        patch("stock_mcp.server.DSAClient") as mock_client,
        patch("stock_mcp.server.uvicorn.run") as mock_uvicorn,
    ):
        mock_client.return_value = MagicMock()
        run()

    assert call_order == ["load_dotenv", "load_config"]
    mock_uvicorn.assert_called_once()
