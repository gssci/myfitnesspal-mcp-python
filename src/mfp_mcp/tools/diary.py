"""MCP tools for diary and water operations."""

import json

from ..app import mcp
from ..auth import get_mfp_client
from ..formatting import (
    format_meal_entry,
    format_nutrition_dict,
    format_response,
    parse_date,
)
from ..models import (
    AddFoodToDiaryInput,
    GetDiaryInput,
    GetWaterInput,
    RemoveFoodFromDiaryInput,
    SetWaterInput,
)
from ..services.diary import (
    add_food_to_diary,
    list_diary_entries,
    remove_food_entry,
    set_water_intake,
)


@mcp.tool(
    name="mfp_get_diary",
    annotations={
        "title": "Get Food Diary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_get_diary(params: GetDiaryInput) -> str:
    """
    Get the food diary for a specific date including all meals and their nutritional information.

    Returns meals (Breakfast, Lunch, Dinner, Snacks) with each food entry's name,
    quantity, and complete nutrition breakdown (calories, protein, carbs, fat, etc.).
    Also includes daily totals and goals.

    Args:
        params: GetDiaryInput containing:
            - date (str, optional): Date in YYYY-MM-DD format, defaults to today
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Formatted diary data with meals, entries, nutrition, and goals
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)

        # Build response data
        data = {
            "date": str(target_date),
            "meals": {},
            "daily_totals": {},
            "daily_goals": {},
            "water": day.water,
            "notes": day.notes or "",
        }

        # Process meals
        for meal in day.meals:
            meal_data = {
                "entries": [format_meal_entry(entry) for entry in meal.entries],
                "totals": format_nutrition_dict(meal.totals),
            }
            data["meals"][meal.name] = meal_data

        # Get daily totals and goals
        totals = {}
        for entry in day.entries:
            for key, value in entry.totals.items():
                val = float(value.magnitude) if hasattr(value, "magnitude") else value
                totals[key] = totals.get(key, 0) + val
        data["daily_totals"] = totals
        data["daily_goals"] = day.goals

        return format_response(data, params.response_format, f"Food Diary for {target_date}")

    except Exception as e:
        return f"Error retrieving diary: {e!s}"


@mcp.tool(
    name="mfp_get_water",
    annotations={
        "title": "Get Water Intake",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_get_water(params: GetWaterInput) -> str:
    """
    Get water intake for a specific date.

    Returns the number of cups/glasses of water logged for the day.

    Args:
        params: GetWaterInput containing:
            - date (str, optional): Date in YYYY-MM-DD format, defaults to today

    Returns:
        str: Water intake amount for the specified date
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)
        day = client.get_date(target_date)

        data = {
            "date": str(target_date),
            "water_cups": day.water,
            "water_ml": day.water * 236.588,  # Convert cups to ml
        }

        return json.dumps(data, indent=2)

    except Exception as e:
        return f"Error getting water intake: {e!s}"


@mcp.tool(
    name="mfp_add_food_to_diary",
    annotations={
        "title": "Add Food to Diary",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def mfp_add_food_to_diary(params: AddFoodToDiaryInput) -> str:
    """
    Add a food item to your MyFitnessPal food diary for a specific date and meal.

    This tool adds a food entry to your diary. You can search for foods using
    mfp_search_food to find the food ID (mfp_id) needed for this tool.

    Args:
        params: AddFoodToDiaryInput containing:
            - mfp_id (str): MyFitnessPal food item ID (from mfp_search_food)
            - meal (str): Meal name - 'Breakfast', 'Lunch', 'Dinner', or 'Snacks' (default: 'Breakfast')
            - date (str, optional): Date in YYYY-MM-DD format, defaults to today
            - quantity (float): Number of servings (default: 1.0)
            - unit (str, optional): Unit/serving size (e.g., '1 cup', '100g')

    Returns:
        str: Confirmation message with details of the added food entry
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)

        # Normalize meal name (capitalize first letter)
        meal = params.meal.strip().capitalize()
        if meal.lower() == "snack":
            meal = "Snacks"

        # Add food to diary
        entry_id = add_food_to_diary(
            client=client,
            mfp_id=params.mfp_id,
            meal=meal,
            target_date=target_date,
            quantity=params.quantity,
            unit=params.unit,
        )

        # Get food details for confirmation
        try:
            food_item = client.get_food_item_details(params.mfp_id)
            food_name = getattr(food_item, "description", "Unknown Food")
        except Exception:
            food_name = "Food item"

        return json.dumps(
            {
                "success": True,
                "message": f"Successfully added {food_name} to {meal}",
                "entry_id": entry_id,
                "date": str(target_date),
                "meal": meal,
                "food_id": params.mfp_id,
                "food_name": food_name,
                "quantity": params.quantity,
                "unit": params.unit,
            },
            indent=2,
        )

    except Exception as e:
        return f"Error adding food to diary: {e!s}"


@mcp.tool(
    name="mfp_remove_food_from_diary",
    annotations={
        "title": "Remove Food From Diary",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def mfp_remove_food_from_diary(params: RemoveFoodFromDiaryInput) -> str:
    """
    Remove (delete) one or more food entries from your diary.

    Two modes:

    1. By entry_id (precise): delete exactly the entry whose
       food_entry_id matches. Use this when you already know the ID.

    2. By name_contains (fuzzy): list the day's entries, find ones whose
       name contains the given substring (case-insensitive), optionally
       restricted to a meal, and delete up to max_matches of them.

    Args:
        params: RemoveFoodFromDiaryInput with one of:
            - entry_id: exact food_entry_id to delete
            - name_contains: substring match against entry names
            - meal: restrict matching to one meal
            - max_matches: safety cap for fuzzy matches (default 1)
            - date: date to operate on (default today)

    Returns:
        JSON describing each entry that was removed.
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)

        # Mode 1: delete a single entry by ID
        if params.entry_id:
            remove_food_entry(client, params.entry_id)
            return json.dumps(
                {
                    "success": True,
                    "removed": [{"entry_id": params.entry_id}],
                    "date": str(target_date),
                },
                indent=2,
            )

        # Mode 2: fuzzy match by name (+ optional meal filter)
        if not params.name_contains:
            return "Error removing food: provide either entry_id or name_contains"

        entries = list_diary_entries(client, target_date)
        needle = params.name_contains.lower()
        meal_filter = params.meal.lower() if params.meal else None

        matches = []
        for e in entries:
            if needle not in e["name"].lower():
                continue
            if meal_filter and meal_filter not in e["meal"].lower():
                continue
            matches.append(e)

        if not matches:
            return json.dumps(
                {
                    "success": False,
                    "removed": [],
                    "message": (
                        f"No entries matched '{params.name_contains}'"
                        + (f" in {params.meal}" if params.meal else "")
                    ),
                },
                indent=2,
            )

        to_remove = matches[: params.max_matches]
        removed = []
        for e in to_remove:
            remove_food_entry(client, e["entry_id"])
            removed.append(
                {
                    "entry_id": e["entry_id"],
                    "name": e["name"],
                    "meal": e["meal"],
                }
            )

        return json.dumps(
            {
                "success": True,
                "removed": removed,
                "matched_count": len(matches),
                "remaining_matches_skipped": max(0, len(matches) - len(to_remove)),
                "date": str(target_date),
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return f"Error removing food: {e}"


@mcp.tool(
    name="mfp_set_water",
    annotations={
        "title": "Log Water Intake",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def mfp_set_water(params: SetWaterInput) -> str:
    """
    Log water intake for a specific date.

    Sets the number of cups of water consumed for the day. MyFitnessPal uses
    cups as the unit (1 cup = ~237ml).

    Args:
        params: SetWaterInput containing:
            - cups (float): Number of cups of water (e.g., 2.5 for 2.5 cups)
            - date (str, optional): Date in YYYY-MM-DD format, defaults to today

    Returns:
        str: Confirmation message with the logged water amount
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)

        # Set water intake
        set_water_intake(client=client, target_date=target_date, cups=params.cups)

        return json.dumps(
            {
                "success": True,
                "message": f"Successfully logged {params.cups} cups of water",
                "date": str(target_date),
                "cups": params.cups,
                "milliliters": round(params.cups * 236.588, 2),
            },
            indent=2,
        )

    except Exception as e:
        return f"Error setting water intake: {e!s}"
