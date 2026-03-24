"""
Tool Executor — Invoke an MCP tool by name with timeout + error handling.

Thin wrapper around tool.ainvoke() that adds:
  - Timeout protection (default 30s)
  - Consistent error formatting
  - Result text extraction from MCP content blocks
"""

import asyncio
import json
import logging

from app.mcp.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30  # seconds


async def execute_tool(
    registry: ToolRegistry,
    tool_name: str,
    args: dict | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """
    Execute an MCP tool by name and return the result as a string.

    Args:
        registry:  The ToolRegistry containing cached tool objects.
        tool_name: Name of the tool to call (e.g. "ZohoBooks_list_invoices").
        args:      Arguments to pass to the tool.
        timeout:   Max seconds to wait for the tool call.

    Returns:
        Result text, or an error message string.
    """
    tool = registry.get_tool(tool_name)
    if not tool:
        return f"Error: tool '{tool_name}' not found in registry"

    try:
        result = await asyncio.wait_for(
            tool.ainvoke(args or {}),
            timeout=timeout,
        )
        return extract_text(result)

    except asyncio.TimeoutError:
        logger.error("Tool '%s' timed out after %ds", tool_name, timeout)
        return f"Error: tool '{tool_name}' timed out after {timeout}s"

    except Exception as e:
        logger.error("Tool '%s' failed: %s", tool_name, e, exc_info=True)
        return f"Error: tool '{tool_name}' failed — {e}"


def extract_text(result) -> str:
    """
    Extract text from an MCP tool result.
    Results come as either:
      - A string
      - A list of content blocks: [{"type": "text", "text": "..."}]
    """
    if isinstance(result, str):
        return result

    if isinstance(result, list):
        texts = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
        return "\n".join(texts) if texts else str(result)

    return str(result)
