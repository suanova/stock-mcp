"""HTTP client over the DSA FastAPI API.

This is the only coupling between stock-mcp and daily_stock_analysis: a thin client over two
stable endpoints.

  POST /api/v1/agent/chat   -> {success, content, session_id, error}  (the 问股 answer)
  GET  /api/v1/agent/skills -> {skills: [{id,name,description}], default_skill_id}

When DSA has ADMIN_AUTH_ENABLED=true the client logs in once via POST /api/v1/auth/login and
reuses the session cookie (httpx keeps the jar). On a mid-call 401 it re-logs in once and
retries. All network/HTTP errors are converted to clean ``DSAClientError`` text so the MCP
tool never raises a raw traceback at the caller.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("stock_mcp.dsa_client")


class DSAClientError(Exception):
    """Raised when a DSA API call cannot be turned into a usable result.

    The ``message`` is safe to surface verbatim to the MCP caller; ``status_code`` and
    ``body`` carry extra detail for logging.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Any = None,
        capability_unsupported: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body
        self.capability_unsupported = capability_unsupported


class DSAClient:
    """A small, reusable HTTP client for the DSA Agent Chat API."""

    def __init__(self, config: "Config") -> None:  # type: ignore[name-defined]
        # config is stock_mcp.config.Config; imported lazily-free via typing here.
        self._config = config
        self._client = httpx.Client(
            base_url=config.api_base,
            timeout=httpx.Timeout(config.api_timeout),
            follow_redirects=True,
        )
        # ask_stock uses a longer timeout; we swap it per-request.
        self._request_timeout = httpx.Timeout(config.api_request_timeout)
        self._logged_in = False

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DSAClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ auth

    def _is_auth_enabled(self) -> bool:
        return self._config.auth_enabled

    def ensure_session(self) -> None:
        """Log in once when auth is enabled. Idempotent and safe to call repeatedly."""
        if not self._is_auth_enabled() or self._logged_in:
            return
        password = self._config.auth_password
        try:
            resp = self._client.post(
                "/api/v1/auth/login",
                json={"password": password, "passwordConfirm": password},
                timeout=self._config.api_timeout,
            )
        except httpx.HTTPError as exc:
            raise DSAClientError(
                f"Could not reach DSA API to log in at {self._config.api_base}: {exc}"
            ) from exc

        if resp.status_code == 401:
            raise DSAClientError(
                "DSA rejected the admin password (POST /api/v1/auth/login returned 401). "
                "Check DSA_API_PASSWORD matches the DSA admin password."
            )
        if resp.status_code >= 400:
            raise DSAClientError(
                f"DSA login failed (HTTP {resp.status_code}): {self._safe_text(resp)}",
                status_code=resp.status_code,
                body=self._safe_json(resp),
            )
        # httpx stores the Set-Cookie session cookie in self._client.cookies.
        self._logged_in = True
        logger.info("Logged in to DSA; session cookie acquired.")

    def _force_relogin(self) -> None:
        """Drop the cached login state and log in again (used after a 401)."""
        self._logged_in = False
        # Clear cookies so a stale session is not reused.
        self._client.cookies.clear()
        self.ensure_session()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _safe_text(resp: httpx.Response) -> str:
        try:
            return resp.text
        except Exception:
            return ""

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return None

    @staticmethod
    def _error_detail(resp: httpx.Response) -> dict:
        """Extract a FastAPI-style error detail dict from a response, if present."""
        body = DSAClient._safe_json(resp)
        if isinstance(body, dict):
            return body
        return {"raw": DSAClient._safe_text(resp)}

    # ------------------------------------------------------------------ tools

    def list_skills(self) -> dict:
        """GET /api/v1/agent/skills -> {skills: [...], default_skill_id}."""
        self.ensure_session()
        try:
            resp = self._client.get(
                "/api/v1/agent/skills", timeout=self._config.api_timeout
            )
        except httpx.HTTPError as exc:
            raise DSAClientError(
                f"Could not reach DSA API at {self._config.api_base}: {exc}"
            ) from exc

        if resp.status_code == 401 and self._is_auth_enabled():
            self._force_relogin()
            resp = self._client.get(
                "/api/v1/agent/skills", timeout=self._config.api_timeout
            )

        if resp.status_code >= 400:
            detail = self._error_detail(resp)
            raise DSAClientError(
                f"GET /api/v1/agent/skills failed (HTTP {resp.status_code}): {detail}",
                status_code=resp.status_code,
                body=detail,
            )
        body = self._safe_json(resp)
        if not isinstance(body, dict):
            raise DSAClientError(
                "GET /api/v1/agent/skills returned a non-JSON response; "
                "is DSA running and reachable?"
            )
        return body

    def ask_stock(
        self,
        question: str,
        *,
        stock_code: Optional[str] = None,
        skills: Optional[list[str]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """POST /api/v1/agent/chat and return the parsed ChatResponse dict.

        Raises ``DSAClientError`` (with ``capability_unsupported=True`` for the Codex
        backend case) on any failure that cannot be turned into an answer.
        """
        self.ensure_session()

        payload: dict[str, Any] = {"message": question}
        if session_id:
            payload["session_id"] = session_id
        if skills:
            payload["skills"] = skills
        if stock_code:
            # The web Chat page sends the current stock as context so the agent grounds
            # its tool calls to it. Mirror that here.
            payload["context"] = {"stock_code": stock_code}

        try:
            resp = self._client.post(
                "/api/v1/agent/chat", json=payload, timeout=self._request_timeout
            )
        except httpx.HTTPError as exc:
            raise DSAClientError(
                f"Could not reach DSA API at {self._config.api_base}: {exc}"
            ) from exc

        if resp.status_code == 401 and self._is_auth_enabled():
            self._force_relogin()
            try:
                resp = self._client.post(
                    "/api/v1/agent/chat", json=payload, timeout=self._request_timeout
                )
            except httpx.HTTPError as exc:
                raise DSAClientError(
                    f"Could not reach DSA API at {self._config.api_base}: {exc}"
                ) from exc

        if resp.status_code >= 400:
            detail = self._error_detail(resp)
            # FastAPI HTTPException detail can be a dict like {"error": ..., "message": ...}
            error_code = ""
            if isinstance(detail, dict):
                error_code = str(detail.get("error") or detail.get("detail", {}).get("error") or "")
            capability = error_code == "capability_unsupported"
            message = self._format_chat_error(resp.status_code, detail, capability)
            raise DSAClientError(
                message,
                status_code=resp.status_code,
                body=detail,
                capability_unsupported=capability,
            )

        body = self._safe_json(resp)
        if not isinstance(body, dict):
            raise DSAClientError(
                "POST /api/v1/agent/chat returned a non-JSON response; "
                "is DSA running and reachable?"
            )
        return body

    @staticmethod
    def _format_chat_error(status: int, detail: dict, capability: bool) -> str:
        if capability:
            return (
                "DSA's configured Agent backend is codex_app_server, which requires the "
                "streaming Chat interface and does not support POST /api/v1/agent/chat. "
                "ask_stock over MCP needs the LiteLLM backend. Switch AGENT_BACKEND to "
                "litellm (or auto) in DSA, or use the DSA web Chat page directly."
            )
        # FastAPI HTTPException often nests the message under detail.
        nested = detail.get("detail") if isinstance(detail, dict) else None
        msg = ""
        if isinstance(nested, dict):
            msg = str(nested.get("message") or nested.get("error") or "")
        elif isinstance(nested, str):
            msg = nested
        if not msg:
            msg = str(detail.get("message") or detail.get("error") or detail)
        return f"ask_stock failed (HTTP {status}): {msg}"
