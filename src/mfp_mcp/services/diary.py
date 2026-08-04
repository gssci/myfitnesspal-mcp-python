"""Food diary and water mutation services."""

import json
import logging
from datetime import date

from ..config import (
    MFP_API_BASE,
    MFP_WEB_BASE,
    VALID_MEALS,
    normalize_meal_name,
    normalize_meal_number,
)
from ..units import is_discrete_serving, normalize_unit, usable_gram_weight
from .food import assess_food_plausibility, get_food_v2, invalidate_meal_food_cache
from .http import _get_csrf_token, _mfp_api_headers, _web_headers

logger = logging.getLogger("mfp_mcp")

MAX_EXPLICIT_SERVINGS = 20
MAX_ENTRY_CALORIES = 5000

# The nutrients a logging confirmation reports, in the order they read best.
# "calories" is spelled "energy" in a food's v2 record and "calories" in the
# diary totals, so the lookup is special-cased in each direction.
MACRO_KEYS = ("calories", "protein", "carbohydrates", "fat")


def resolve_food_amount(
    food: dict,
    amount: float,
    unit: str,
) -> tuple[dict, float]:
    """Convert a user-facing amount into an MFP serving size and serving count.

    For example, a request for 250 g against a database serving of 60 g is
    converted to 250 / 60 = 4.1667 servings. Unknown units fail closed instead
    of silently falling back to a different serving.
    """
    serving_sizes = food.get("serving_sizes") or []
    if not serving_sizes:
        raise RuntimeError(f"Food {food.get('id')} has no serving sizes")

    wanted = normalize_unit(unit)
    if wanted == "serving":
        if amount > MAX_EXPLICIT_SERVINGS:
            raise RuntimeError(
                f"Refusing {amount:g} database servings. For a physical amount, "
                "use its real unit such as g, ml, or count."
            )
        chosen = serving_sizes[0]
        servings = float(amount)
    else:
        if wanted == "count":
            chosen = next((size for size in serving_sizes if is_discrete_serving(size)), None)
        else:
            chosen = next(
                (
                    size
                    for size in serving_sizes
                    if normalize_unit(str(size.get("unit", ""))) == wanted
                ),
                None,
            )

        # Some entries expose gram_weight even when the serving is named
        # "scoop", "piece", etc. That still permits exact gram conversion.
        if chosen is None and wanted in {"g", "kg"}:
            chosen = next(
                (size for size in serving_sizes if usable_gram_weight(size) is not None),
                None,
            )

        if chosen is None:
            available = ", ".join(
                f"{size.get('value')} {size.get('unit')}" for size in serving_sizes
            )
            raise RuntimeError(
                f"Unit {unit!r} is not available for this food. Available servings: {available}"
            )

        chosen_unit = normalize_unit(str(chosen.get("unit", "")))
        if wanted in {"g", "kg"}:
            requested_grams = float(amount) * (1000 if wanted == "kg" else 1)
            # MFP's gram_weight is not consistently a weight: for some foods it
            # mirrors nutrition_multiplier (e.g. a 60 g serving reports 1.0).
            # An explicit g/kg serving value is therefore authoritative.
            if chosen_unit == "g":
                units_per_serving = float(chosen["value"])
            elif chosen_unit == "kg":
                units_per_serving = float(chosen["value"]) * 1000
            elif (gram_weight := usable_gram_weight(chosen)) is not None:
                units_per_serving = gram_weight
            else:
                raise RuntimeError(f"Cannot convert serving {chosen.get('unit')!r} to grams")
            servings = requested_grams / units_per_serving
        else:
            # For 2 cups against a database serving of 0.5 cup, log 4 servings.
            serving_value = float(chosen.get("value") or 0)
            if serving_value <= 0:
                raise RuntimeError("The selected serving has an invalid value")
            servings = float(amount) / serving_value

    if not 0 < servings <= 5000:
        raise RuntimeError(
            f"Calculated serving count {servings:.4g} is outside the safe range (0, 5000]. "
            "Check the requested amount and unit."
        )

    diary_serving = {
        "value": chosen["value"],
        "unit": chosen["unit"],
        "nutrition_multiplier": chosen["nutrition_multiplier"],
    }
    return diary_serving, servings


def entry_nutrition(food: dict, serving: dict, servings: float) -> dict[str, float]:
    """Scale a food's stated macros to the amount actually being logged.

    The v2 record was already fetched to perform the write, so this costs no
    extra request. Nutrients the record does not carry are left out rather
    than reported as zero.
    """
    nutrition = food.get("nutritional_contents") or {}
    try:
        multiplier = float(serving.get("nutrition_multiplier")) * float(servings)
    except (TypeError, ValueError):
        return {}
    if multiplier < 0:
        return {}

    scaled: dict[str, float] = {}
    for key in MACRO_KEYS:
        raw = nutrition.get("energy") if key == "calories" else nutrition.get(key)
        try:
            value = float(raw.get("value") if isinstance(raw, dict) else raw)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        scaled[key] = round(value * multiplier, 1)
    return scaled


def estimate_entry_calories(food: dict, serving: dict, servings: float) -> float | None:
    """Estimate total calories using MFP's base energy and serving multiplier."""
    return entry_nutrition(food, serving, servings).get("calories")


def _macro_totals(nutrition: dict) -> dict[str, float]:
    """Keep only the reported macros, in reading order, as plain floats."""
    totals: dict[str, float] = {}
    for key in MACRO_KEYS:
        if key not in nutrition:
            continue
        value = nutrition[key]
        magnitude = float(value.magnitude) if hasattr(value, "magnitude") else float(value)
        totals[key] = round(magnitude, 1)
    return totals


def _find_meal(day, meal: str):
    """Locate a meal on a scraped day, by name and then by diary position.

    MyFitnessPal renders meal headings in the account's own language, so an
    English name match cannot be relied on. Position is the fallback because
    the diary always lists its meals in the 0-3 order the meal number encodes.
    """
    wanted = normalize_meal_name(meal)
    meals = list(day.meals)
    for candidate in meals:
        if str(candidate.name).strip().casefold() == str(wanted).strip().casefold():
            return candidate
    index = normalize_meal_number(meal)
    if isinstance(index, int) and 0 <= index < len(meals):
        return meals[index]
    return None


def get_diary_totals(client, target_date: date, meal: str | None = None) -> dict:
    """Read the day's calories and macros, and one meal's, in a single fetch.

    Deliberately narrower than ``mfp_get_diary``: a logging confirmation needs
    the running totals, not every entry of every meal. ``meal`` is None when
    only the day is wanted, and the meal is reported as None when the diary
    holds no such meal.
    """
    day = client.get_date(target_date)
    return {
        "meal": _macro_totals(found.totals) if (found := _find_meal(day, meal)) else None,
        "day": _macro_totals(day.totals),
    }


def add_food_to_diary(
    client,
    mfp_id: str,
    meal: str,
    target_date: date,
    amount: float = 1.0,
    unit: str = "serving",
) -> dict:
    """
    Add a food item to the diary for a specific date and meal.

    Args:
        client: Authenticated myfitnesspal.Client instance
        mfp_id: MyFitnessPal food item ID
        meal: Meal name (Breakfast, Lunch, Dinner, Snacks)
        target_date: Date to add the food entry
        amount: Physical amount expressed in ``unit`` (e.g. 250)
        unit: Requested unit (e.g. "g", "oz", "cup", or "serving")

    Returns:
        The entry id and the resolved MFP serving details

    Raises:
        RuntimeError: If the operation fails
    """
    food = get_food_v2(client, mfp_id)
    plausibility = assess_food_plausibility(food)
    if plausibility["status"] == "implausible":
        raise RuntimeError(
            "Refusing to add a nutritionally implausible database entry: "
            + " ".join(plausibility["warnings"])
        )
    serving_size, servings = resolve_food_amount(food, amount, unit)
    nutrition = entry_nutrition(food, serving_size, servings)
    estimated_calories = nutrition.get("calories")
    if estimated_calories is not None and estimated_calories > MAX_ENTRY_CALORIES:
        raise RuntimeError(
            f"Refusing an entry estimated at {estimated_calories:.0f} kcal. "
            "This usually means grams or item counts were passed as database servings."
        )

    # Accepts a meal number as well as a name: this endpoint wants a name, the
    # history endpoints want a number, and callers mix them up constantly.
    meal_name = normalize_meal_name(meal)
    if meal_name not in VALID_MEALS:
        raise RuntimeError(
            f"Invalid meal {meal!r}. Expected one of: {', '.join(VALID_MEALS)}, "
            "or a meal number 0-3."
        )

    entry = {
        "type": "food_entry",
        "date": target_date.strftime("%Y-%m-%d"),
        "meal_name": meal_name,
        "servings": servings,
        "food": {"id": str(food["id"]), "version": str(food["version"])},
        "serving_size": serving_size,
    }

    response = client.session.post(
        f"{MFP_API_BASE}/v2/diary",
        headers=_mfp_api_headers(client, json_body=True),
        data=json.dumps({"items": [entry]}),
        timeout=30,
    )

    if response.status_code not in (200, 201):
        detail = ""
        try:
            body = response.json()
            detail = body.get("error_details", {}).get("item_error") or body.get(
                "error_description", ""
            )
        except Exception:
            pass
        raise RuntimeError(
            f"Failed to add food to diary: HTTP {response.status_code}"
            + (f" - {detail}" if detail else "")
        )

    invalidate_meal_food_cache(client)

    logger.info(
        f"Added food {mfp_id} ({serving_size['value']} {serving_size['unit']} "
        f"x{servings:.6g}; requested {amount} {unit}) to {meal_name} for {target_date}"
    )

    # Keep the returned UUID so callers can verify or remove this exact entry
    # through the modern webpage diary service.
    try:
        entry_id = response.json()["items"][0]["id"]
    except (ValueError, KeyError, IndexError):
        logger.warning("Entry created but MyFitnessPal returned no entry id")
        entry_id = None

    return {
        "entry_id": entry_id,
        "food_name": food.get("description", "Food item"),
        "requested_amount": float(amount),
        "requested_unit": unit,
        "database_serving": {
            "value": serving_size["value"],
            "unit": serving_size["unit"],
        },
        "servings": round(servings, 8),
        "estimated_calories": estimated_calories,
        # This entry's own contribution, so a caller can report what it just
        # logged without reading the food record back.
        "nutrition": nutrition,
    }


def list_diary_entries(client, target_date: date) -> list[dict[str, str]]:
    """
    Read the webpage diary service and return food entries for a date.

    Returns:
        List of {"entry_id", "name", "meal"} dicts in display order.
    """
    date_str = target_date.strftime("%Y-%m-%d")
    csrf = _get_csrf_token(client)
    response = client.session.get(
        f"{MFP_WEB_BASE}/api/services/diary/read_diary",
        params={"entry_date": date_str, "types": "food_entry"},
        headers=_web_headers(csrf),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not read diary entries: HTTP {response.status_code}")

    entries: list[dict[str, str]] = []
    for raw_entry in response.json() or []:
        if raw_entry.get("type") != "food_entry" or not raw_entry.get("id"):
            continue
        food = raw_entry.get("food") or {}
        if isinstance(food, dict) and isinstance(food.get("item"), dict):
            food = food["item"]
        name = food.get("description", "") if isinstance(food, dict) else ""
        entries.append(
            {
                "entry_id": str(raw_entry["id"]),
                "name": str(name),
                "meal": str(raw_entry.get("meal_name") or ""),
            }
        )
    return entries


def remove_food_entry(client, entry_id: str) -> None:
    """
    Delete a food diary entry by its food_entry_id.

    Uses the same cookie-authenticated webpage service that creates and reads
    modern UUID-based entries.
    """
    csrf = _get_csrf_token(client)
    response = client.session.delete(
        f"{MFP_WEB_BASE}/api/services/diary/{entry_id}",
        headers=_web_headers(csrf),
        timeout=30,
    )
    if response.status_code in (200, 204):
        logger.info(f"Removed diary entry {entry_id}")
        return
    raise RuntimeError(f"Remove failed for entry {entry_id}: HTTP {response.status_code}")


def set_water_intake(client, target_date: date, cups: float) -> None:
    """
    Set water intake for a specific date.

    Args:
        client: Authenticated myfitnesspal.Client instance
        target_date: Date to set water intake
        cups: Number of cups of water

    Raises:
        RuntimeError: If the operation fails
    """
    from urllib import parse

    try:
        # Get the diary page for the target date to extract CSRF token
        date_str = target_date.strftime("%Y-%m-%d")
        diary_url = parse.urljoin(
            client.BASE_URL_SECURE, f"food/diary/{client.effective_username}?date={date_str}"
        )

        # Use the library's method to get the document
        document = client._get_document_for_url(diary_url)

        # Extract authenticity token
        authenticity_token = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
        if not authenticity_token:
            raise RuntimeError("Could not find authenticity token on diary page")
        authenticity_token = authenticity_token[0]

        # Build the URL for setting water
        # MyFitnessPal uses /food/diary/{username}/water endpoint
        water_url = parse.urljoin(
            client.BASE_URL_SECURE, f"food/diary/{client.effective_username}/water"
        )

        # Prepare the data for the POST request
        post_data = {
            "authenticity_token": authenticity_token,
            "date": date_str,
            "water": str(cups),
        }

        # Set water intake
        headers = {
            "Referer": diary_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

        response = client.session.post(water_url, data=post_data, headers=headers)
        response.raise_for_status()

        if response.status_code != 200:
            raise RuntimeError(f"Failed to set water: HTTP {response.status_code}")

        logger.info(f"Successfully set water intake to {cups} cups for {target_date}")

    except Exception as e:
        # Don't expose internal error details to avoid leaking sensitive information
        error_msg = str(e)
        # Only include safe error information
        if "HTTP" in error_msg or "status" in error_msg.lower():
            raise RuntimeError(f"Failed to set water intake: {error_msg}")
        else:
            raise RuntimeError(
                "Failed to set water intake. Please check your authentication and try again."
            )
