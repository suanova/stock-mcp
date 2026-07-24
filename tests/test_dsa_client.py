"""Offline unit tests for the DSA HTTP client (no live DSA required).

Uses ``httpx.MockTransport`` to simulate DSA API responses. Pins the contract between
stock-mcp and DSA's /api/v1/agent/chat + /api/v1/agent/skills endpoints.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from stock_mcp.config import Config
from stock_mcp.dsa_client import DSAClient, DSAClientError


def _make_config(auth_enabled: bool = False, password: str = "") -> Config:
    """Build a minimal test config."""
    return Config(
        api_base_url="http://test-dsa",
        auth_enabled=auth_enabled,
        auth_password=password,
        mcp_host="127.0.0.1",
        mcp_port=8765,
        mcp_path="/mcp",
        api_timeout=30.0,
        api_request_timeout=120.0,
        log_level="INFO",
    )


def _json_response(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


def _text_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text)


# --------------------------------------------------------------------------- auth disabled


def test_ask_stock_success_without_auth() -> None:
    """A happy path where DSA returns a complete answer."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.url.path == "/api/v1/agent/chat"
        body = json.loads(request.content)
        assert body["message"] == "What about 600519?"
        assert body.get("context") == {"stock_code": "600519"}
        return _json_response({
            "success": True,
            "content": "贵州茅台是一只不错的股票。",
            "session_id": "sess-123",
            "error": None,
        })

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    result = client.ask_stock("What about 600519?", stock_code="600519")
    assert result["success"] is True
    assert result["content"] == "贵州茅台是一只不错的股票。"
    assert result["session_id"] == "sess-123"
    # No auth calls should have been made.
    assert calls == [("POST", "http://test-dsa/api/v1/agent/chat")]


def test_ask_stock_returns_success_false() -> None:
    """DSA chat succeeds at the HTTP layer but reports a logic failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({
            "success": False,
            "content": "",
            "session_id": "sess-456",
            "error": "Agent mode not enabled",
        })

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    result = client.ask_stock("What about 600519?")
    assert result["success"] is False
    assert result["error"] == "Agent mode not enabled"


def test_ask_stock_capability_unsupported() -> None:
    """Codex backend rejects POST /chat with a structured 400."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({
            "error": "capability_unsupported",
            "message": "Codex Agent requires the Chat interface with progress and stop support",
        }, status=400)

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ask_stock("What about 600519?")

    err = exc_info.value
    assert err.status_code == 400
    assert err.capability_unsupported is True
    assert "codex_app_server" in err.message.lower() or "liteLLM" in err.message


def test_ask_stock_unexpected_error() -> None:
    """Non-JSON 500 from DSA yields a clean error (no raw traceback leak)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response("Internal Server Error", status=500)

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ask_stock("What about 600519?")

    err = exc_info.value
    assert err.status_code == 500
    assert "ask_stock failed (HTTP 500)" in err.message


def test_list_skills_without_auth() -> None:
    """GET /agent/skills returns the expected shape."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/agent/skills"
        return _json_response({
            "skills": [
                {"id": "bull_trend", "name": "Bull Trend", "description": "Trend following."},
                {"id": "growth", "name": "Growth", "description": "Growth quality."},
            ],
            "default_skill_id": "bull_trend",
        })

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    body = client.list_skills()
    assert len(body["skills"]) == 2
    assert body["default_skill_id"] == "bull_trend"


# --------------------------------------------------------------------------- auth enabled


def test_auth_login_then_ask_stock() -> None:
    """Auth enabled: login first, then ask_stock succeeds on the second request."""
    seq = iter([
        # 1) login
        ("login", _json_response({"loggedIn": True})),
        # 2) ask_stock
        ("chat", _json_response({
            "success": True,
            "content": "OK",
            "session_id": "sess-auth",
            "error": None,
        })),
    ])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        step, response = next(seq)
        if step == "login":
            body = json.loads(request.content)
            assert body["password"] == "admin123"
            assert body["passwordConfirm"] == "admin123"
        return response

    cfg = _make_config(auth_enabled=True, password="admin123")
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    result = client.ask_stock("hello")
    assert result["success"] is True
    assert result["session_id"] == "sess-auth"
    assert "/api/v1/auth/login" in calls
    assert "/api/v1/agent/chat" in calls


def test_auth_401_retries_once_and_succeeds() -> None:
    """A stale session triggers one re-login, then the retry succeeds."""
    seq = iter([
        # login (initial)
        ("login", _json_response({"loggedIn": True})),
        # chat -> 401 (session expired on DSA side)
        ("chat-401", _json_response({"error": "unauthorized"}, status=401)),
        # re-login
        ("relogin", _json_response({"loggedIn": True})),
        # retry chat -> success
        ("chat-ok", _json_response({
            "success": True,
            "content": "After retry",
            "session_id": "sess-retry",
            "error": None,
        })),
    ])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        step, response = next(seq)
        return response

    cfg = _make_config(auth_enabled=True, password="admin123")
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    result = client.ask_stock("hello")
    assert result["content"] == "After retry"
    assert calls.count("/api/v1/auth/login") == 2


def test_auth_401_after_retry_gives_up() -> None:
    """If the retry also 401s, the client surfaces a clear error instead of looping."""
    seq = iter([
        ("login", _json_response({"loggedIn": True})),
        ("chat-401a", _json_response({"error": "unauthorized"}, status=401)),
        ("relogin", _json_response({"loggedIn": True})),
        ("chat-401b", _json_response({"error": "unauthorized"}, status=401)),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        _, response = next(seq)
        return response

    cfg = _make_config(auth_enabled=True, password="admin123")
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ask_stock("hello")

    err = exc_info.value
    assert err.status_code == 401
    assert "unauthorized" in err.message.lower() or "401" in err.message


def test_auth_login_bad_password_fails_fast() -> None:
    """Startup login with a bad password produces a clean, actionable error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"error": "unauthorized"}, status=401)

    cfg = _make_config(auth_enabled=True, password="wrong")
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ensure_session()

    err = exc_info.value
    assert "rejected the admin password" in err.message


# --------------------------------------------------------------------------- network edge cases


def test_ask_stock_network_error() -> None:
    """Network unreachable produces a clean error without a raw traceback."""
    cfg = _make_config()
    client = DSAClient(cfg)
    # Override with a transport that always raises.
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused"))),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ask_stock("hello")

    assert "Could not reach DSA API" in exc_info.value.message


def test_ask_stock_non_json_response() -> None:
    """DSA returns HTML (wrong URL/proxy) -> clean error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response("<html>...not found...</html>", status=404)

    cfg = _make_config()
    client = DSAClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.api_base,
    )

    with pytest.raises(DSAClientError) as exc_info:
        client.ask_stock("hello")

    assert "ask_stock failed (HTTP 404)" in exc_info.value.message
