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

# Every measure MyFitnessPal can name, so that whatever is left over is
# genuinely countable. A gap here is not cosmetic: an unlisted weight unit is
# read as a discrete item, which both mislabels it in count_units and lets
# unit="count" resolve to it, silently logging milligrams instead of fruit.
_PHYSICAL_UNIT_PREFIXES = {
    "g",
    "mg",
    "kg",
    "lb",
    "lbs",
    "pound",
    "pounds",
    "oz",
    "ml",
    "cl",
    "dl",
    "l",
    "liter",
    "liters",
    "litre",
    "litres",
    "fl oz",
    "floz",
    "pint",
    "pints",
    "quart",
    "quarts",
    "gallon",
    "gallons",
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


def units_match(requested: str, serving_unit: str) -> bool:
    """Whether a requested unit names the same measure as a serving's unit.

    MyFitnessPal spells its servings however the contributor typed them, so
    "2 slices" against a serving of "1 slice" is the same request. Folding a
    trailing plural keeps that from failing as an unavailable unit; short
    words are left alone so "oz" and "g" are never truncated.
    """

    def singular(unit: str) -> str:
        normalized = normalize_unit(unit)
        if len(normalized) > 3 and normalized.endswith("s"):
            return normalized[:-1]
        return normalized

    return singular(requested) == singular(serving_unit)


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
