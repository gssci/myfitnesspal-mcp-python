"""Normalize the human-facing measurement units used by MCP tools."""

_UNIT_ALIASES = {
    "g.": "g",
    "gr": "g",
    "gr.": "g",
    "gram": "g",
    "grams": "g",
    "gramme": "g",
    "grammes": "g",
    "grammo": "g",
    "grammi": "g",
    "kg.": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "chilogrammo": "kg",
    "chilogrammi": "kg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitro": "ml",
    "millilitri": "ml",
    "ounce": "oz",
    "ounces": "oz",
    "oncia": "oz",
    "once": "oz",
    "servings": "serving",
    "portion": "serving",
    "portions": "serving",
    "porzione": "serving",
    "porzioni": "serving",
}


def normalize_unit(unit: str) -> str:
    normalized = " ".join(unit.strip().casefold().split())
    return _UNIT_ALIASES.get(normalized, normalized)


def is_gram_unit(unit: str) -> bool:
    return normalize_unit(unit) == "g"
