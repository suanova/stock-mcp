"""FastMCP server exposing DSA's 问股 (Agent Chat) as an MCP tool over Streamable HTTP.

Tools:
  - ask_stock(question, stock_code?, skills?, session_id?) -> text answer from DSA Agent Chat
  - list_agent_skills() -> compact list of available strategy skills

The server is a thin client over the running DSA FastAPI API; it does not import any DSA
source. Run with ``python -m stock_mcp`` or the ``dsa-mcp-server`` console script.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from stock_mcp.config import Config, load_config
from stock_mcp.dsa_client import DSAClient, DSAClientError

logger = logging.getLogger("stock_mcp")


_CONSOLE_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)s [%(name)s:%(lineno)d] %(message)s"


def _setup_logging(
    level: str,
    log_file: Optional[str] = None,
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backup_count: int = 3,
) -> None:
    """Configure root logging to stderr and optionally to a file.

    When ``log_file`` is provided, logs are written to both stderr and the file.
    The file handler rotates when ``log_max_bytes`` is exceeded; set it to ``0``
    for a plain ever-growing file.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Avoid duplicate stderr handlers if _setup_logging is called more than once.
    has_console = any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        for h in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_CONSOLE_FMT))
        root.addHandler(console)

    if log_file:
        log_path = Path(log_file)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not create log directory %s: %s", log_path.parent, exc
            )
        else:
            if log_max_bytes > 0:
                fh: logging.Handler = logging.handlers.RotatingFileHandler(
                    log_path,
                    maxBytes=log_max_bytes,
                    backupCount=log_backup_count,
                    encoding="utf-8",
                )
            else:
                fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FILE_FMT))
            root.addHandler(fh)

    # Quiet noisy HTTP loggers; stock-mcp logs its own call summaries.
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# A single client + config shared across tool calls within one server process.
# Built lazily in run() and stored on the FastMCP instance via a closure.
_client: Optional[DSAClient] = None
_config: Optional[Config] = None


def build_server(config: Config, client: DSAClient) -> FastMCP:
    """Construct the FastMCP server with tools wired to the given DSA client.

    Kept separate from ``run`` so tests can build a server against an injected client
    without binding a socket.
    """
    global _client, _config
    _client = client
    _config = config

    mcp = FastMCP(
        name="DSA 问股 (ask-stock)",
        instructions=(
            "Ask DSA's Agent Chat (问股) about stocks. Use ask_stock for a natural-language "
            "stock question; pass stock_code to ground the answer to a specific stock, and "
            "optional skills (strategy IDs from list_agent_skills) to select analysis "
            "strategies. The tool returns the agent's final text answer plus a session_id "
            "that can be passed back for multi-turn conversation. Requires the DSA API "
            "server to be running and reachable."
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                *config.mcp_allowed_hosts,
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool()
    def ask_stock(
        question: str,
        stock_code: Optional[str] = None,
        skills: Optional[list[str]] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Ask DSA's Agent Chat (问股) a stock question and return the final answer.

        Args:
            question: The natural-language stock question (required).
            stock_code: Optional stock code to ground the answer, e.g. "600519",
                "hk00700", or "AAPL". When provided, the agent targets this stock.
            skills: Optional list of strategy skill IDs (see list_agent_skills) to apply,
                e.g. ["bull_trend", "growth_quality"]. Omit to use DSA's default selection.
            session_id: Optional session ID to continue a prior conversation. A new
                session is created when omitted. The returned text includes the session_id
                so callers can thread multi-turn chat.

        Returns:
            The agent's final text answer, with a trailing metadata line
            (session_id / model / steps) when available. On failure, returns a clear
            human-readable error string rather than raising.
        """
        if _client is None:  # defensive; build_server always sets it
            return "ask_stock is not initialized (no DSA client)."

        try:
            result = _client.ask_stock(
                question=question,
                stock_code=stock_code,
                skills=skills,
                session_id=session_id,
            )
        except DSAClientError as exc:
            logger.warning("ask_stock failed: %s", exc.message)
            return exc.message
        except Exception as exc:  # noqa: BLE001 - never let a tool crash the server
            logger.exception("ask_stock unexpected error")
            return f"ask_stock encountered an unexpected error: {exc}"

        success = bool(result.get("success"))
        content = str(result.get("content") or "")
        error = result.get("error")
        sid = result.get("session_id")

        if not success:
            return f"ask_stock failed: {error or 'unknown error (no error field returned)'}"

        # Trailing metadata line so callers can thread multi-turn chat and see provenance.
        meta_bits = []
        if sid:
            meta_bits.append(f"session_id={sid}")
        if result.get("model"):
            meta_bits.append(f"model={result['model']}")
        if result.get("total_steps") is not None:
            meta_bits.append(f"steps={result['total_steps']}")
        meta_line = f"\n({', '.join(meta_bits)})" if meta_bits else ""
        return f"{content}{meta_line}"

    @mcp.tool()
    def list_agent_skills() -> str:
        """List the strategy skills available to ask_stock.

        Returns a compact text list (one skill per line: "id — name: description") plus
        the default skill ID. Use the returned ids as the ``skills`` argument to ask_stock.
        """
        if _client is None:
            return "list_agent_skills is not initialized (no DSA client)."

        try:
            body = _client.list_skills()
        except DSAClientError as exc:
            logger.warning("list_agent_skills failed: %s", exc.message)
            return exc.message
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_agent_skills unexpected error")
            return f"list_agent_skills encountered an unexpected error: {exc}"

        skills = body.get("skills") or []
        default_id = body.get("default_skill_id") or ""
        lines = []
        for sk in skills:
            sid = sk.get("id") or sk.get("name") or ""
            name = sk.get("name") or ""
            desc = sk.get("description") or ""
            lines.append(f"{sid} — {name}: {desc}".rstrip(": "))
        if default_id:
            lines.append(f"\ndefault_skill_id: {default_id}")
        if not skills:
            return "No agent skills are configured in DSA. ask_stock can still be called without skills."
        return "\n".join(lines)

    return mcp


def run() -> None:
    """Load config, build the DSA client, and serve the MCP server over Streamable HTTP."""
    config = load_config()
    _setup_logging(
        config.log_level,
        config.log_file,
        config.log_max_bytes,
        config.log_backup_count,
    )
    logger.info(
        "stock-mcp starting: DSA API at %s (auth=%s), MCP at http://%s:%d%s",
        config.api_base,
        "on" if config.auth_enabled else "off",
        config.mcp_host,
        config.mcp_port,
        config.mcp_path,
    )

    client = DSAClient(config)
    # Eagerly validate auth so a bad password fails fast at startup, not on first call.
    try:
        client.ensure_session()
    except DSAClientError as exc:
        logger.error("Startup auth check failed: %s", exc.message)
        # Continue starting anyway; non-auth calls and later retries may still succeed,
        # and surfacing the error at startup is more useful than refusing to boot.
        # If auth is required, the first tool call will report the same error clearly.

    mcp = build_server(config, client)

    # FastMCP's streamable HTTP transport returns an ASGI app; serve it with uvicorn.
    # The path FastMCP mounts the Streamable HTTP app at is controlled via run() kwargs
    # in newer SDK versions; mount_path must be URL-safe and start with '/'.
    import uvicorn

    app = mcp.streamable_http_app()

    # The MCP Streamable HTTP endpoint is served at the app root; clients use
    # http://<host>:<port><mcp_path>. We expose mcp_path as the expected client URL
    # and log it; FastMCP's streamable_http_app serves at "/" by default.
    # log_config=None lets uvicorn propagate to our root handlers so file logging
    # also captures uvicorn/access logs.
    uvicorn.run(
        app,
        host=config.mcp_host,
        port=config.mcp_port,
        log_level=config.log_level.lower(),
        log_config=None,
    )
    logger.info(
        "stock-mcp listening at http://%s:%d (MCP endpoint: %s)",
        config.mcp_host,
        config.mcp_port,
        config.mcp_path,
    )


if __name__ == "__main__":
    run()
