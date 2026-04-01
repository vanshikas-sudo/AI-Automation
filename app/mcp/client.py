"""
MCP Client — Pure connection management.

Handles:
  - Transport negotiation (streamable_http → sse fallback)
  - Retry with exponential backoff
  - Connection health check
  - Clean shutdown + lazy reconnection
"""

import asyncio
import logging
from datetime import timedelta

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

_TRANSPORTS = ("streamable_http", "sse")
_MAX_RETRIES = 5
_RETRY_DELAY = 3  # seconds (doubles each retry)


class MCPClient:
    """Manages the raw MCP server connection lifecycle."""

    def __init__(self):
        self._client: MultiServerMCPClient | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _try_connect(self, url: str, transport: str) -> list | None:
        """Attempt a single connection with the given transport. Returns tools or None."""
        client = None
        try:
            conn_cfg: dict = {"transport": transport, "url": url}
            conn_cfg["timeout"] = timedelta(seconds=120)
            conn_cfg["sse_read_timeout"] = timedelta(seconds=600)

            client = MultiServerMCPClient({"zoho": conn_cfg})
            tools = await asyncio.wait_for(client.get_tools(), timeout=120)
            self._client = client
            self._connected = True
            logger.info(
                "MCP connected (%s) — %d raw tools available",
                transport, len(tools),
            )
            return tools
        except asyncio.TimeoutError:
            logger.warning("MCP connect (%s) timed out after 120s", transport)
        except Exception as e:
            logger.warning("MCP connect (%s) error: %s", transport, e)
        return None

    async def connect(self, url: str) -> list:
        """
        Connect to the MCP server and return the raw (unfiltered) tool list.
        Tries streamable_http first, then falls back to sse.
        Each transport is attempted up to _MAX_RETRIES times with exponential backoff.
        Never raises — returns [] on total failure.
        """
        last_error: str | None = None
        for transport in _TRANSPORTS:
            for attempt in range(1, _MAX_RETRIES + 1):
                logger.info(
                    "MCP connect attempt %d/%d (%s) to %s",
                    attempt, _MAX_RETRIES, transport, url[:80],
                )
                tools = await self._try_connect(url, transport)
                if tools is not None:
                    return tools

                last_error = f"{transport} attempt {attempt}"

                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAY * (2 ** (attempt - 1))
                    logger.info("Retrying in %ds…", delay)
                    await asyncio.sleep(delay)

            logger.warning("Transport '%s' exhausted after %d attempts", transport, _MAX_RETRIES)

        logger.error(
            "Could not connect to MCP server after all retries — "
            "agent will run without tools. Last: %s",
            last_error,
        )
        return []

    async def ensure_connected(self, url: str) -> list:
        """Lazy reconnect if the connection was dropped. Returns tool list."""
        if self._connected:
            return []
        return await self.connect(url)

    async def close(self) -> None:
        """Cleanly shut down the MCP client connection."""
        if self._client:
            self._client = None
            self._connected = False
            logger.info("MCP client disconnected")
