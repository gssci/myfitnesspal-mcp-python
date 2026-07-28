"""Shared FastMCP application and logging setup."""

import logging
import sys

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)

mcp = FastMCP("myfitnesspal_mcp")
