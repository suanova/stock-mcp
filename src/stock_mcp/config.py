"""Environment-driven configuration for stock-mcp.

All tunables are read from environment variables (with optional .env loading done by the
caller). Nothing is hardcoded; every value has a safe default so the server runs with no
configuration in the common local case (DSA API open on 127.0.0.1:8000, no admin auth).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got: {raw!r}")


@dataclass(frozen=True)
class Config:
    """Resolved configuration for one server run."""

    # DSA API
    api_base_url: str
    auth_enabled: bool
    auth_password: str

    # MCP transport
    mcp_host: str
    mcp_port: int
    mcp_path: str

    # Timeouts (seconds)
    api_timeout: float
    api_request_timeout: float

    # Logging
    log_level: str

    @property
    def api_base(self) -> str:
        """api_base_url with any trailing slash stripped, for clean path joining."""
        return self.api_base_url.rstrip("/")

    def validate(self) -> None:
        """Fail fast on a configuration that cannot work."""
        if self.auth_enabled and not self.auth_password:
            raise ValueError(
                "DSA_API_AUTH_ENABLED=true requires DSA_API_PASSWORD to be set "
                "(the DSA admin password used for POST /api/v1/auth/login)."
            )
        if not self.mcp_path.startswith("/"):
            raise ValueError(f"DSA_MCP_PATH must start with '/', got: {self.mcp_path!r}")


def load_config() -> Config:
    """Read configuration from environment variables."""
    cfg = Config(
        api_base_url=os.getenv("DSA_API_BASE_URL", "http://127.0.0.1:8000"),
        auth_enabled=_get_bool("DSA_API_AUTH_ENABLED", False),
        auth_password=os.getenv("DSA_API_PASSWORD", ""),
        mcp_host=os.getenv("DSA_MCP_HOST", "127.0.0.1"),
        mcp_port=_get_int("DSA_MCP_PORT", 8765),
        mcp_path=os.getenv("DSA_MCP_PATH", "/mcp"),
        api_timeout=float(_get_int("DSA_API_TIMEOUT", 30)),
        api_request_timeout=float(_get_int("DSA_API_REQUEST_TIMEOUT", 600)),
        log_level=os.getenv("DSA_MCP_LOG_LEVEL", "INFO").upper(),
    )
    cfg.validate()
    return cfg
