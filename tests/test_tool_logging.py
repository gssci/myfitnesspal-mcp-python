import logging
import pytest
from unittest.mock import AsyncMock, patch

from mfp_mcp import server


@pytest.mark.asyncio
async def test_call_tool_logs_tool_name_and_arguments(caplog):
    caplog.set_level(logging.INFO, logger="mfp_mcp")

    with patch.object(
        server.mcp._tool_manager, "call_tool", new_callable=AsyncMock
    ) as mock_call:
        mock_call.return_value = []
        await server.mcp.call_tool("mfp_get_diary", {"date": "2026-01-01"})

        mock_call.assert_called_once_with(
            "mfp_get_diary",
            {"date": "2026-01-01"},
            context=server.mcp.get_context(),
            convert_result=True,
        )

    log_messages = [rec.message for rec in caplog.records if rec.name == "mfp_mcp"]
    assert any(
        "Calling MCP tool 'mfp_get_diary' with arguments: {'date': '2026-01-01'}"
        in msg
        for msg in log_messages
    )


@pytest.mark.asyncio
async def test_call_tool_logs_success_with_timing_and_preview(caplog):
    caplog.set_level(logging.INFO, logger="mfp_mcp")

    with patch.object(
        server.mcp._tool_manager, "call_tool", new_callable=AsyncMock
    ) as mock_call:
        mock_call.return_value = [type("Block", (), {"text": "Successfully added entry"})()]
        await server.mcp.call_tool("mfp_add_food_to_diary", {"amount": 250})

    log_messages = [rec.message for rec in caplog.records if rec.name == "mfp_mcp"]
    assert any(
        "MCP tool 'mfp_add_food_to_diary' completed in" in msg and "Successfully added entry" in msg
        for msg in log_messages
    )


@pytest.mark.asyncio
async def test_call_tool_logs_caught_errors_as_warnings(caplog):
    caplog.set_level(logging.INFO, logger="mfp_mcp")

    with patch.object(
        server.mcp._tool_manager, "call_tool", new_callable=AsyncMock
    ) as mock_call:
        mock_call.return_value = [
            type("Block", (), {"text": "Error searching foods: boom"})()
        ]
        await server.mcp.call_tool("mfp_search_food", {"query": "banana"})

    warning_records = [
        rec for rec in caplog.records if rec.name == "mfp_mcp" and rec.levelno == logging.WARNING
    ]
    assert any(
        "mfp_search_food" in rec.message and "Error searching foods: boom" in rec.message
        for rec in warning_records
    )


@pytest.mark.asyncio
async def test_call_tool_logs_raised_exceptions(caplog):
    caplog.set_level(logging.INFO, logger="mfp_mcp")

    with patch.object(
        server.mcp._tool_manager, "call_tool", new_callable=AsyncMock
    ) as mock_call:
        mock_call.side_effect = RuntimeError("connection lost")
        with pytest.raises(RuntimeError, match="connection lost"):
            await server.mcp.call_tool("mfp_get_report", {})

    error_records = [
        rec for rec in caplog.records if rec.name == "mfp_mcp" and rec.levelno == logging.ERROR
    ]
    assert any("mfp_get_report" in rec.message and "raised" in rec.message for rec in error_records)
