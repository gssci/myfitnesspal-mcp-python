"""Live regression test for adding a food to a meal and cleaning it up."""

import asyncio
import json

import pytest

from mfp_mcp import server


@pytest.mark.live
def test_add_food_to_snacks_is_visible_and_removable():
    server.load_environment()
    client = server.get_mfp_client()
    target_date = server.parse_date()
    food = server.search_foods_web(client, "chicken breast", limit=1)[0]
    entry_id = None

    try:
        result = asyncio.run(
            server.mfp_add_food_to_diary(
                server.AddFoodToDiaryInput(
                    mfp_id=food["mfp_id"],
                    meal="Snacks",
                    date=str(target_date),
                    amount=0.01,
                    unit="serving",
                )
            )
        )
        payload = json.loads(result)
        assert payload["success"] is True
        entry_id = payload["entry_id"]

        assert entry_id
        entries = server.list_diary_entries(client, target_date)
        created = [entry for entry in entries if entry["entry_id"] == entry_id]
        assert len(created) == 1
        assert created[0]["meal"] == "Snacks"
    finally:
        if entry_id:
            server.remove_food_entry(client, entry_id)

    remaining = server.list_diary_entries(client, target_date)
    assert all(entry["entry_id"] != entry_id for entry in remaining)
