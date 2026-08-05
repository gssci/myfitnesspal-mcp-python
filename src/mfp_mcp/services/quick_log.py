"""Find one food and log it in a single call.

Choosing between search results is a mechanical job: match the words the user
said against a candidate's name, check the candidate supports the unit they
spoke in, reject nutritionally impossible records, take the best one left. The
agent used to do that by reading every candidate into its context and thinking
about it, which cost four model round trips per food — meal history, resolve,
search, add — each one re-reading the whole conversation so far.

Doing it here costs the same HTTP requests and no model round trips at all, so
a food goes from four turns to one. The model keeps the job it is actually good
at: turning "due etti di petto di pollo" into ("petto di pollo", 200, "g").
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..config import normalize_meal_name, normalize_meal_number
from ..units import normalize_unit
from .diary import (
    add_food_to_diary,
    count_sent_as_weight,
    entry_nutrition,
    get_diary_totals,
    resolve_food_amount,
)
from .food import (
    _food_name_key,
    _format_serving_size,
    _resolve_history_id_from_search,
    assess_food_plausibility,
    get_foods_v2,
    get_meal_foods,
    search_food_records,
)
from .http import MfpSessionExpiredError

logger = logging.getLogger("mfp_mcp")

# Each history candidate costs one search request to map its classic id onto a
# modern one, so only the closest couple are worth resolving.
HISTORY_CANDIDATES = 2
SEARCH_CANDIDATES = 8
ALTERNATIVES_REPORTED = 3

# A history row is only preferred over a fresh search when it carries every word
# of the request. Anything looser matches "pollo" against "brodo di pollo".
HISTORY_MATCH_THRESHOLD = 1.0
# Below this a search hit is not the food that was asked for at all.
SEARCH_MATCH_THRESHOLD = 0.5


def _match_score(query: str, *texts: Any) -> float:
    """Fraction of the request's words that a candidate's own text carries."""
    wanted = _food_name_key(query).split()
    if not wanted:
        return 0.0
    have = set(_food_name_key(" ".join(str(text) for text in texts if text)).split())
    return sum(1 for word in wanted if word in have) / len(wanted)


def _units_to_try(unit: str) -> list[str]:
    """The requested unit, then "count" when it names a whole item.

    The caller no longer sees a food's serving table before naming a unit, so
    it can only repeat the user's own word: "slice" against a food that spells
    it "fetta". Falling back to the generic item unit rescues that, and is safe
    precisely because it is only reached for units that are not a weight, a
    volume, or an explicit serving count.
    """
    if normalize_unit(unit) in {"g", "kg", "mg", "ml", "serving", "count"}:
        return [unit]
    return [unit, "count"]


def _evaluate(food: dict[str, Any], amount: float, unit: str) -> tuple[str | None, str]:
    """Return the unit this food can actually take, or why it can take none.

    Runs the same three checks ``add_food_to_diary`` runs before writing, so a
    candidate is only offered up if the write would actually be accepted.
    """
    if assess_food_plausibility(food)["status"] == "implausible":
        return None, "nutrition is physically implausible"
    reason = ""
    for candidate_unit in _units_to_try(unit):
        try:
            serving, servings = resolve_food_amount(food, amount, candidate_unit)
        except RuntimeError as exc:
            reason = str(exc)
            continue
        calories = entry_nutrition(food, serving, servings).get("calories")
        if mistake := count_sent_as_weight(food, amount, candidate_unit, calories):
            reason = mistake
            continue
        return candidate_unit, ""
    return None, reason


def _summarize(food: dict[str, Any], source: str) -> dict[str, Any]:
    """Name a candidate compactly enough to report several of them."""
    servings = food.get("serving_sizes") or []
    return {
        "name": food.get("description"),
        "brand": food.get("brand_name"),
        "mfp_id": str(food.get("id")),
        "units": [_format_serving_size(serving) for serving in servings[:4]],
        "from": source,
    }


def _history_records(client, query: str, meal_number: int) -> list[dict[str, Any]]:
    """Resolve the closest meal-history rows to full v2 food records.

    A session MyFitnessPal has stopped honouring is reported as an expired
    session by ``get_meal_foods``. That must not abort the whole log: the search
    surfaces authenticate off a different session and may still answer, so this
    degrades to "no history candidates" and lets the search decide.
    """
    try:
        lists = get_meal_foods(client, meal_number)
    except (MfpSessionExpiredError, RuntimeError) as exc:
        logger.info(f"Meal history unavailable for {query!r}, searching instead ({exc})")
        return []

    seen: set[str] = set()
    scored: list[tuple[float, dict[str, Any]]] = []
    for source in ("recent", "frequent"):
        for item in lists.get(source) or []:
            key = _food_name_key(item["name"])
            if key in seen:
                continue
            seen.add(key)
            score = _match_score(query, item["name"])
            if score >= HISTORY_MATCH_THRESHOLD:
                scored.append((score, item))
    if not scored:
        return []
    scored.sort(key=lambda pair: pair[0], reverse=True)

    ids: list[str] = []
    for _, item in scored[:HISTORY_CANDIDATES]:
        try:
            mfp_id = _resolve_history_id_from_search(
                client, item["history_id"], item["name"], meal_number
            )
        except (MfpSessionExpiredError, RuntimeError) as exc:
            logger.info(f"Could not resolve history row {item['name']!r} ({exc})")
            continue
        # Older rows point at food records MyFitnessPal has dropped from its
        # index; those simply do not become candidates.
        if mfp_id and mfp_id not in ids:
            ids.append(mfp_id)
    if not ids:
        return []
    found = get_foods_v2(client, ids)
    return [found[mfp_id] for mfp_id in ids if mfp_id in found]


def _ranked_candidates(
    client, query: str, meal_number: int, amount: float, unit: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (usable, rejected) candidates, best first.

    Meal history is consulted first and short-circuits the search when it
    yields something usable: a food the user has logged into this very meal
    before beats anything the global database can offer, and skipping the
    search saves a request as well as the ranking.
    """
    usable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    # History rows were already held to HISTORY_MATCH_THRESHOLD on the name the
    # diary shows, so they are re-scored here only to order them: the resolved
    # record's description drops the brand prefix and would score lower.
    for source, records, threshold in (
        ("history", _history_records(client, query, meal_number), 0.0),
        ("search", None, SEARCH_MATCH_THRESHOLD),
    ):
        if records is None:
            if usable:
                break
            records = search_food_records(client, query, SEARCH_CANDIDATES)

        scored: list[tuple[float, dict[str, Any]]] = []
        for rank, food in enumerate(records):
            score = _match_score(query, food.get("description"), food.get("brand_name"))
            if score < threshold:
                continue
            exact = _food_name_key(food.get("description")) == _food_name_key(query)
            # Rank descending, then the database's own ordering as the tiebreak.
            scored.append(((score, exact, -rank), food))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        for _, food in scored:
            usable_unit, reason = _evaluate(food, amount, unit)
            if usable_unit is None:
                rejected.append({**_summarize(food, source), "why_not": reason})
            else:
                usable.append({**_summarize(food, source), "unit": usable_unit})

    return usable, rejected


def log_food(
    client,
    *,
    food: str,
    amount: float,
    unit: str,
    meal: str,
    target_date: date,
) -> dict[str, Any]:
    """Find the food the user described and add it to their diary."""
    meal_name = normalize_meal_name(meal)
    meal_number = normalize_meal_number(meal)

    usable, rejected = _ranked_candidates(client, food, meal_number, amount, unit)
    if not usable:
        return {
            "success": False,
            "error": (
                f"No food matching {food!r} can take {amount:g} {unit}."
                if rejected
                else f"Nothing in the database matched {food!r}."
            ),
            # Naming what was turned down and why is what lets the model fix a
            # wrong unit, or ask the user, instead of retrying the same call.
            "considered": [
                {key: value for key, value in candidate.items() if key != "from"}
                for candidate in rejected[:ALTERNATIVES_REPORTED]
            ],
        }

    chosen = usable[0]
    result = add_food_to_diary(
        client=client,
        mfp_id=chosen["mfp_id"],
        meal=meal_name,
        target_date=target_date,
        amount=amount,
        # The unit this candidate was verified against, which is the requested
        # one unless it only answers to the generic item unit.
        unit=chosen["unit"],
    )

    payload: dict[str, Any] = {
        "success": True,
        "message": (
            f"Successfully added {result['requested_amount']:g} "
            f"{result['requested_unit']} of {result['food_name']} to {meal_name}"
        ),
        "date": str(target_date),
        "meal": meal_name,
        "food_id": chosen["mfp_id"],
        "matched_from": chosen["from"],
        "entry_id": result.get("entry_id"),
        "nutrition": result.get("nutrition"),
    }

    # The food is already logged, so a totals read that fails must not turn a
    # successful write into a reported failure.
    try:
        totals = get_diary_totals(client, target_date, meal_name)
        payload["meal_totals"] = totals["meal"]
        payload["day_totals"] = totals["day"]
    except Exception as exc:
        payload["meal_totals"] = None
        payload["day_totals"] = None
        payload["day_totals_error"] = str(exc)

    if len(usable) > 1:
        # Just the names: enough for the model to offer a correction if the user
        # says "no, the Milbona one", without printing a second search listing.
        payload["other_matches"] = [
            f"{candidate['name']} ({candidate['mfp_id']})"
            for candidate in usable[1:ALTERNATIVES_REPORTED + 1]
        ]
    return payload
