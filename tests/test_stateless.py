"""Tests for stateless/sync MCP mode and the optional JSON-RPC id middleware."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from stock_mcp.server import _optional_jsonrpc_id_middleware


@pytest.mark.anyio
async def test_middleware_adds_id_to_jsonrpc_request():
    """A JSON-RPC request without an id gets id: 0 injected."""
    inner = AsyncMock()
    middleware = _optional_jsonrpc_id_middleware(inner)

    scope = {
        "type": "http",
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list"}).encode(),
            "more_body": False,
        }

    send = AsyncMock()
    await middleware(scope, receive, send)

    assert inner.called
    called_scope, called_receive, called_send = inner.call_args[0]

    # Content-Length should be updated to match the modified body.
    headers = {name.lower(): value for name, value in called_scope["headers"]}
    assert b"content-length" in headers

    received_body = b""
    while True:
        msg = await called_receive()
        received_body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break

    data = json.loads(received_body)
    assert data["id"] == 0
    assert data["method"] == "tools/list"


@pytest.mark.anyio
async def test_middleware_preserves_existing_id():
    """A JSON-RPC request that already has an id is left unchanged."""
    inner = AsyncMock()
    middleware = _optional_jsonrpc_id_middleware(inner)

    original = json.dumps({"jsonrpc": "2.0", "id": 42, "method": "tools/list"}).encode()
    scope = {
        "type": "http",
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {"type": "http.request", "body": original, "more_body": False}

    await middleware(scope, receive, AsyncMock())

    called_scope, called_receive, _ = inner.call_args[0]
    received_body = b""
    while True:
        msg = await called_receive()
        received_body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break

    assert received_body == original


@pytest.mark.anyio
async def test_middleware_ignores_non_jsonrpc_bodies():
    """Non-JSON-RPC payloads are passed through untouched."""
    inner = AsyncMock()
    middleware = _optional_jsonrpc_id_middleware(inner)

    original = b"plain text"
    scope = {
        "type": "http",
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": original, "more_body": False}

    await middleware(scope, receive, AsyncMock())

    _, called_receive, _ = inner.call_args[0]
    received_body = b""
    while True:
        msg = await called_receive()
        received_body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break

    assert received_body == original


@pytest.mark.anyio
async def test_middleware_passthrough_for_non_http():
    """Non-HTTP scopes (e.g., lifespans/websockets) are forwarded directly."""
    inner = AsyncMock()
    middleware = _optional_jsonrpc_id_middleware(inner)

    scope = {"type": "lifespan"}
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    inner.assert_awaited_once_with(scope, receive, send)
