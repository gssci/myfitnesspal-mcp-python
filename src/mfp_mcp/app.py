"""Shared FastMCP application and logging setup."""

from collections.abc import Sequence
import logging
import sys
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock

logger = logging.getLogger("mfp_mcp")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)

_PREVIEW_LIMIT = 200


def _preview_result(result: Sequence[ContentBlock] | dict[str, Any]) -> str:
    """Collapse a tool result into a single-line, length-capped preview for logs."""
    if isinstance(result, dict):
        text = str(result)
    else:
        parts: list[str] = []
        for block in result:
            text_value = getattr(block, "text", None)
            if text_value is None and isinstance(block, dict):
                text_value = block.get("text")
            parts.append(str(text_value) if text_value is not None else str(block))
        text = " | ".join(parts)
    text = " ".join(text.split())
    return text if len(text) <= _PREVIEW_LIMIT else text[: _PREVIEW_LIMIT - 1] + "…"


class LoggingFastMCP(FastMCP):
    """FastMCP server subclass that logs tool calls, timing, and outcomes.

    Every call is logged at INFO before it runs. Once it finishes, a second
    line reports elapsed time and a preview of the result; a result whose text
    starts with "Error " (this codebase's convention for caught exceptions) or
    an actual raised exception is logged at WARNING/ERROR instead, so failures
    are visible in server logs even though tools normally return them as
    strings rather than raising.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        logger.info("Calling MCP tool '%s' with arguments: %s", name, arguments)
        started = time.monotonic()
        try:
            result = await super().call_tool(name, arguments)
        except Exception:
            elapsed = time.monotonic() - started
            logger.exception("MCP tool '%s' raised after %.3fs", name, elapsed)
            raise
        elapsed = time.monotonic() - started
        preview = _preview_result(result)
        if preview.startswith("Error "):
            logger.warning(
                "MCP tool '%s' returned an error after %.3fs: %s", name, elapsed, preview
            )
        else:
            logger.info(
                "MCP tool '%s' completed in %.3fs: %s", name, elapsed, preview
            )
        return result


mcp = LoggingFastMCP("myfitnesspal_mcp")
