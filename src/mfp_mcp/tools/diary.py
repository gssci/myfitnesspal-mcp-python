"""MCP tools for diary and water operations."""

import json

from ..auth import get_mfp_client
from ..config import normalize_meal_name
from ..flat_schema import flat_tool
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
    get_diary_totals,
    list_diary_entries,
    remove_food_entry,
    set_water_intake,
)


@flat_tool(
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
    """Get a dated diary with meal entries, nutrition totals, and goals."""
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


@flat_tool(
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


@flat_tool(
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
    """Add one food using an ID from search or meal-history resolution.

    amount is the physical quantity in unit, not a database-serving multiplier:
    250 grams is amount=250/unit="g"; 2 whole fruits is amount=2/unit="count".
    Pick the unit that matches how the food was described, then check the food
    supports it. unit="count" resolves to whatever that food calls one item, so
    a food offering "1 fruit" is already the right match for "1 kiwi".
    Use unit="serving" only when the user explicitly says servings/portions.

    Returns "nutrition" (this entry's calories and macros), "meal_totals" and
    "day_totals" (that meal's and the whole day's, both including this entry),
    so confirming an add never needs a follow-up mfp_get_diary call.
    """
    try:
        client = get_mfp_client()
        target_date = parse_date(params.date)

        # Already normalized by the model; kept explicit for direct callers.
        meal = normalize_meal_name(params.meal)

        # Add food to diary
        result = add_food_to_diary(
            client=client,
            mfp_id=params.mfp_id,
            meal=meal,
            target_date=target_date,
            amount=params.amount,
            unit=params.unit,
        )

        payload = {
            "success": True,
            "message": (
                f"Successfully added {result['requested_amount']:g} "
                f"{result['requested_unit']} of {result['food_name']} to {meal}"
            ),
            "date": str(target_date),
            "meal": meal,
            "food_id": params.mfp_id,
            **result,
        }

        # The food is already logged at this point, so a totals read that fails
        # must not turn a successful write into a reported failure.
        try:
            totals = get_diary_totals(client, target_date, meal)
            payload["meal_totals"] = totals["meal"]
            payload["day_totals"] = totals["day"]
        except Exception as e:
            payload["meal_totals"] = None
            payload["day_totals"] = None
            payload["day_totals_error"] = str(e)

        return json.dumps(payload, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@flat_tool(
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
    """Remove diary food by exact entry_id or fuzzy name_contains.

    Fuzzy removal can be limited by meal and max_matches (default 1).
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


@flat_tool(
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
