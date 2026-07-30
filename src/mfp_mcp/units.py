"""Normalize the human-facing measurement units used by MCP tools."""

import math
from typing import Any

_UNIT_ALIASES = {
    "g.": "g",
    "gr": "g",
    "gr.": "g",
    "gram": "g",
    "grams": "g",
    "gramme": "g",
    "grammes": "g",
    "gram(s)": "g",
    "grammo": "g",
    "grammi": "g",
    "kg.": "kg",
    "kg(s)": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "chilogrammo": "kg",
    "chilogrammi": "kg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitro": "ml",
    "millilitri": "ml",
    "ml(s)": "ml",
    "ounce": "oz",
    "ounces": "oz",
    "ounce(s)": "oz",
    "oncia": "oz",
    "once": "oz",
    "servings": "serving",
    "portion": "serving",
    "portions": "serving",
    "porzione": "serving",
    "porzioni": "serving",
    "counts": "count",
    "each": "count",
    "unit": "count",
    "units": "count",
    "unità": "count",
    "piece": "count",
    "pieces": "count",
    "pezzo": "count",
    "pezzi": "count",
    "item": "count",
    "items": "count",
    "fruit": "count",
    "fruits": "count",
    "frutto": "count",
    "frutti": "count",
    "whole": "count",
}

_PHYSICAL_UNIT_PREFIXES = {
    "g",
    "kg",
    "ml",
    "l",
    "oz",
    "fl oz",
    "cup",
    "cups",
    "tsp",
    "teaspoon",
    "teaspoons",
    "tbsp",
    "tablespoon",
    "tablespoons",
    "serving",
}


def normalize_unit(unit: str) -> str:
    normalized = " ".join(unit.strip().casefold().split())
    return _UNIT_ALIASES.get(normalized, normalized)


def is_gram_unit(unit: str) -> bool:
    return normalize_unit(unit) == "g"


def is_count_unit(unit: str) -> bool:
    return normalize_unit(unit) == "count"


def usable_gram_weight(serving: dict[str, Any]) -> float | None:
    """Return a trustworthy gram weight for a named serving, if present.

    MFP sometimes copies ``nutrition_multiplier`` into ``gram_weight``. The
    common value 1 then incorrectly turns 50 g into 50 database portions.
    """
    try:
        gram_weight = float(serving.get("gram_weight") or 0)
        multiplier = float(serving.get("nutrition_multiplier") or 0)
    except (TypeError, ValueError):
        return None
    if gram_weight <= 0:
        return None
    if gram_weight <= 10 and math.isclose(gram_weight, multiplier):
        return None
    return gram_weight


def is_discrete_serving(serving: dict[str, Any]) -> bool:
    """Whether a serving represents countable items rather than a measure."""
    unit = normalize_unit(str(serving.get("unit", "")))
    if unit == "count":
        return True
    if any(
        unit == prefix or unit.startswith(f"{prefix} ") or unit.startswith(f"{prefix},")
        for prefix in _PHYSICAL_UNIT_PREFIXES
    ):
        return False
    return bool(unit)
