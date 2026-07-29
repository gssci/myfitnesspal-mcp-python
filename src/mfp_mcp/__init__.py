"""
MyFitnessPal MCP Server

A Model Context Protocol (MCP) server for interacting with MyFitnessPal data.
"""

__version__ = "1.0.0"
__author__ = "Adam"

__all__ = ["__version__", "mcp"]


def __getattr__(name: str):
    """Load the composed server lazily so ``python -m mfp_mcp.server`` is clean."""
    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(name)
