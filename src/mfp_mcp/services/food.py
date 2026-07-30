"""Food lookup, search, and custom-food services."""

import json
import logging
import re
from datetime import date
from time import monotonic
from typing import Any
from urllib.parse import quote

from lxml import html as lxml_html

from ..browser_cookies import try_chromium_browsers_for_session_cookies
from ..config import (
    MFP_API_BASE,
    MFP_BROWSER_USER_AGENT,
    MFP_FOOD_SEARCH_PAGE,
    MFP_WEB_BASE,
)
from ..cookie_store import dict_to_cookiejar, save_cookies
from ..units import (
    is_discrete_serving,
    is_gram_unit,
    normalize_unit,
    usable_gram_weight,
)
from .http import _api_error_detail, _get_csrf_token, _mfp_api_headers, _web_headers

logger = logging.getLogger("mfp_mcp")

_DIRECT_SERVING_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s+(.+?)\s*$")
_MAX_PLAUSIBLE_KCAL_PER_100 = 1000.0
_MEAL_FOOD_CACHE_TTL_SECONDS = 60
_MEAL_FOOD_CACHE: dict[tuple[int, int], tuple[float, dict[str, list[dict[str, Any]]]]] = {}

_NUTRIENT_KEYS = {
    "fat": "fat",
    "saturated_fat": "saturated_fat",
    "polyunsaturated_fat": "polyunsaturated_fat",
    "monounsaturated_fat": "monounsaturated_fat",
    "trans_fat": "trans_fat",
    "cholesterol": "cholesterol",
    "sodium": "sodium",
    "potassium": "potassium",
    "carbs": "carbohydrates",
    "fiber": "fiber",
    "sugar": "sugar",
    "protein": "protein",
    "vitamin_a": "vitamin_a",
    "vitamin_c": "vitamin_c",
    "calcium": "calcium",
    "iron": "iron",
}


def get_food_v2(client, mfp_id: str) -> dict[str, Any]:
    """
    Fetch a food's full v2 record, including its version and serving sizes.

    The diary API rejects entries whose food version does not match the
    current stored version, so this must be read fresh rather than cached.

    Args:
        client: Authenticated myfitnesspal.Client instance
        mfp_id: MyFitnessPal food item ID

    Returns:
        The food object as returned by the v2 API

    Raises:
        RuntimeError: If the food cannot be retrieved
    """
    response = client.session.get(
        f"{MFP_API_BASE}/v2/foods",
        params={"ids": str(mfp_id)},
        headers=_mfp_api_headers(client),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not look up food {mfp_id}: HTTP {response.status_code}")

    items = response.json().get("items") or []
    if not items:
        raise RuntimeError(f"No food found with ID {mfp_id}")
    return items[0]


def select_serving_size(food: dict[str, Any], unit: str | None = None) -> dict[str, Any]:
    """
    Choose which of a food's serving sizes to log against.

    Args:
        food: Food object from get_food_v2
        unit: Optional unit to match (e.g. "oz", "medium breast"). Matching is
            case-insensitive and accepts a substring. Falls back to the food's
            default (first) serving size when omitted or unmatched.

    Returns:
        The serving size dict, trimmed to the fields the diary API permits

    Raises:
        RuntimeError: If the food declares no serving sizes
    """
    serving_sizes = food.get("serving_sizes") or []
    if not serving_sizes:
        raise RuntimeError(f"Food {food.get('id')} has no serving sizes")

    chosen = serving_sizes[0]
    if unit:
        wanted = unit.strip().lower()
        for size in serving_sizes:
            size_unit = str(size.get("unit", "")).lower()
            if size_unit == wanted or wanted in size_unit:
                chosen = size
                break
        else:
            logger.warning(
                f"Unit {unit!r} not found for food {food.get('id')}; "
                f"using default serving {chosen.get('unit')!r}"
            )

    # The diary endpoint rejects any serving_size field beyond these three.
    return {
        "value": chosen["value"],
        "unit": chosen["unit"],
        "nutrition_multiplier": chosen["nutrition_multiplier"],
    }


def _extract_food_search_items(content: bytes, query: str) -> list[dict[str, Any]]:
    """Extract food results from the search page's server-rendered state."""
    document = lxml_html.fromstring(content)
    scripts = document.xpath("//script[@id='__NEXT_DATA__']/text()")
    if not scripts:
        raise RuntimeError("MyFitnessPal search page contained no Next.js state")

    try:
        page_data = json.loads(scripts[0])
        queries = page_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise RuntimeError("MyFitnessPal search page state had an unexpected format") from e

    wanted = query.casefold()
    for cached_query in queries:
        query_key = cached_query.get("queryKey") or []
        if (
            len(query_key) >= 2
            and query_key[0] == "food"
            and str(query_key[1]).casefold() == wanted
        ):
            data = cached_query.get("state", {}).get("data", {})
            items = data.get("items") if isinstance(data, dict) else None
            return items if isinstance(items, list) else []

    raise RuntimeError(f"MyFitnessPal search page contained no results state for {query!r}")


def _format_serving_size(serving: dict[str, Any]) -> str:
    """Format the primary serving the same way the website displays it."""
    value = serving.get("value")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(part) for part in (value, serving.get("unit")) if part is not None)


def _food_name_key(value: Any) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value).casefold()).split())


def serving_capabilities(serving_sizes: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose compact, model-friendly unit choices without serving multipliers."""
    count_units = list(
        dict.fromkeys(
            str(serving.get("unit"))
            for serving in serving_sizes
            if is_discrete_serving(serving)
        )
    )
    return {
        "serving_options": [
            {"amount": serving.get("value"), "unit": serving.get("unit")}
            for serving in serving_sizes[:5]
        ],
        "supports_grams": any(
            normalize_unit(str(serving.get("unit", ""))) in {"g", "kg"}
            or usable_gram_weight(serving) is not None
            for serving in serving_sizes
        ),
        "supports_count": bool(count_units),
        "count_units": count_units,
    }


def assess_food_plausibility(food: dict[str, Any]) -> dict[str, Any]:
    """Flag physically impossible energy density in user-contributed foods.

    Energy and ``nutrition_multiplier`` are combined for every explicit gram
    or millilitre serving. A generous 1,000 kcal/100 g (or ml) ceiling avoids
    false positives while still catching unit errors such as 800 kcal per gram.
    """
    nutrition = food.get("nutritional_contents") or {}
    energy = nutrition.get("energy") or {}
    try:
        base_calories = float(energy.get("value"))
    except (TypeError, ValueError):
        return {"status": "unknown", "warnings": ["No usable calorie value was provided."]}

    densities: list[tuple[str, float]] = []
    for serving in food.get("serving_sizes") or []:
        unit = str(serving.get("unit") or "")
        normalized = unit.strip().casefold()
        try:
            value = float(serving.get("value"))
            multiplier = float(serving.get("nutrition_multiplier", 1))
        except (TypeError, ValueError):
            continue
        if value <= 0 or multiplier < 0:
            continue
        if is_gram_unit(unit):
            physical_amount = value
            physical_unit = "g"
        elif normalize_unit(unit) == "kg":
            physical_amount = value * 1000
            physical_unit = "g"
        elif normalized in {"ml", "milliliter", "milliliters", "millilitro", "millilitri"}:
            physical_amount = value
            physical_unit = "ml"
        elif (gram_weight := usable_gram_weight(serving)) is not None:
            physical_amount = gram_weight
            physical_unit = "g"
        else:
            continue
        densities.append(
            (physical_unit, base_calories * multiplier / physical_amount * 100)
        )

    if not densities:
        return {"status": "unknown", "warnings": []}

    worst_unit, worst_density = max(densities, key=lambda item: item[1])
    rounded = round(worst_density, 2)
    if worst_density > _MAX_PLAUSIBLE_KCAL_PER_100:
        return {
            "status": "implausible",
            "calories_per_100_g_or_ml": rounded,
            "warnings": [
                (
                    f"Reported energy is about {rounded:g} kcal per 100 {worst_unit}, "
                    "which is physically implausible and likely a serving/unit error."
                )
            ],
        }
    return {
        "status": "plausible",
        "calories_per_100_g_or_ml": rounded,
        "warnings": [],
    }


def _parse_history_fragment(content: bytes, source: str) -> list[dict[str, Any]]:
    """Parse one classic recent/frequent-food HTML fragment."""
    document = lxml_html.fromstring(content)
    results: list[dict[str, Any]] = []
    rows = document.xpath("//tr[contains(concat(' ', normalize-space(@class), ' '), ' favorite ')]")
    for row in rows:
        checkboxes = row.xpath(
            ".//input[contains(concat(' ', normalize-space(@class), ' '), ' checkbox ')]"
        )
        cells = row.xpath("./td")
        if not checkboxes or len(cells) < 3:
            continue
        checkbox = checkboxes[0]
        history_id = checkbox.get("data-food-id")
        if not history_id:
            continue
        quantity_nodes = row.xpath(".//input[contains(@name, '[quantity]')]")
        select_nodes = row.xpath(".//select[contains(@name, '[weight_id]')]")
        options = select_nodes[0].xpath(".//option") if select_nodes else []
        serving_names = [" ".join(option.text_content().split()) for option in options]
        selected = next(
            (
                " ".join(option.text_content().split())
                for option in options
                if option.get("selected") is not None
            ),
            serving_names[0] if serving_names else None,
        )
        quantity = quantity_nodes[0].get("value") if quantity_nodes else None
        try:
            previous_quantity: float | None = float(str(quantity).replace(",", "."))
        except (TypeError, ValueError):
            previous_quantity = None
        results.append(
            {
                "history_id": str(history_id),
                "name": " ".join(cells[1].text_content().split()),
                "source": source,
                "verified": checkbox.get("data-food-verified") == "true",
                "previous_quantity": previous_quantity,
                "previous_serving": selected,
                "available_servings": serving_names,
                "supports_grams": any(
                    bool(match := _DIRECT_SERVING_RE.match(name)) and is_gram_unit(match.group(2))
                    for name in serving_names
                ),
                "supports_count": any(
                    bool(match := _DIRECT_SERVING_RE.match(name))
                    and is_discrete_serving(
                        {"value": match.group(1).replace(",", "."), "unit": match.group(2)}
                    )
                    for name in serving_names
                ),
            }
        )
    return results


def get_meal_foods(client, meal: int) -> dict[str, list[dict[str, Any]]]:
    """Return the account's recent and frequent foods for one meal."""
    cache_key = (id(client), meal)
    cached = _MEAL_FOOD_CACHE.get(cache_key)
    if cached and monotonic() - cached[0] <= _MEAL_FOOD_CACHE_TTL_SECONDS:
        return cached[1]

    headers = {"Accept": "text/html", "X-Requested-With": "XMLHttpRequest"}
    output: dict[str, list[dict[str, Any]]] = {}
    for source, endpoint in (("recent", "load_recent"), ("frequent", "load_most_used")):
        response = client.session.post(
            f"{MFP_WEB_BASE}/food/{endpoint}",
            data={"meal": meal, "base_index": 0, "page": 1},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Could not load {source} foods: HTTP {response.status_code}")
        output[source] = _parse_history_fragment(response.content, source)
    _MEAL_FOOD_CACHE[cache_key] = (monotonic(), output)
    return output


def invalidate_meal_food_cache(client) -> None:
    """Discard cached recent/frequent lists after a diary mutation."""
    client_id = id(client)
    for key in [key for key in _MEAL_FOOD_CACHE if key[0] == client_id]:
        del _MEAL_FOOD_CACHE[key]


def _resolve_history_id_from_search(client, history_id: str, name: str, meal: int) -> str | None:
    """Bridge a classic history ID to the modern numeric v2 food ID."""
    search_url = f"{MFP_WEB_BASE}/food/search"
    page = client.session.get(search_url, timeout=30)
    if page.status_code != 200:
        raise RuntimeError(f"Could not open classic food search: HTTP {page.status_code}")
    document = lxml_html.fromstring(page.content)
    tokens = document.xpath("(//input[@name='authenticity_token']/@value)[1]")
    if not tokens:
        raise RuntimeError("Classic food search contained no authenticity token")
    response = client.session.post(
        search_url,
        data={
            "authenticity_token": tokens[0],
            "search": name,
            "date": date.today().isoformat(),
            "meal": str(meal),
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not resolve meal food: HTTP {response.status_code}")
    results = lxml_html.fromstring(response.content)
    matches = results.xpath(f"//a[@data-original-id='{history_id}']/@data-external-id")
    return str(matches[0]) if matches else None


def resolve_meal_food(client, history_id: str, meal: int) -> dict[str, Any]:
    """Resolve and nutrition-check a food selected from meal history."""
    lists = get_meal_foods(client, meal)
    candidate = next(
        (
            item
            for source in ("recent", "frequent")
            for item in lists[source]
            if item["history_id"] == history_id
        ),
        None,
    )
    if candidate is None:
        return {"resolved": False, "reason": "history_id_not_found_for_meal"}
    mfp_id = _resolve_history_id_from_search(client, history_id, candidate["name"], meal)
    if mfp_id is None:
        return {
            "resolved": False,
            "reason": "modern_id_not_found",
            "name": candidate["name"],
            "fallback": "Use mfp_search_food and validate the selected result.",
        }
    food = get_food_v2(client, mfp_id)
    energy = ((food.get("nutritional_contents") or {}).get("energy") or {}).get("value")
    serving_sizes = food.get("serving_sizes") or []
    return {
        "resolved": True,
        "mfp_id": str(mfp_id),
        "name": food.get("description") or candidate["name"],
        "brand": food.get("brand_name"),
        "verified": bool(food.get("verified")),
        "calories": energy,
        "servings": [_format_serving_size(s) for s in serving_sizes],
        **serving_capabilities(serving_sizes),
        "nutrition_plausibility": assess_food_plausibility(food),
    }


def search_foods_web(client, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search foods through the cookie-authenticated MyFitnessPal webpage."""
    url = f"{MFP_FOOD_SEARCH_PAGE}/{quote(query, safe='')}"

    def load_page():
        return client.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": MFP_BROWSER_USER_AGENT,
            },
            timeout=30,
        )

    response = load_page()

    # A cookie set can still satisfy the legacy diary endpoint while NextAuth
    # considers it incomplete and redirects modern web pages to /account/logout.
    # Refresh from the browser in that case, then persist only after the search
    # page itself proves the replacement session works.
    if "/account/logout" in str(getattr(response, "url", "")):
        browser_session = try_chromium_browsers_for_session_cookies()
        if browser_session:
            _, browser_cookies = browser_session
            client.session.cookies.clear()
            client.session.cookies.update(dict_to_cookiejar(browser_cookies))
            response = load_page()
            if response.status_code == 200 and "/account/logout" not in str(response.url):
                save_cookies(browser_cookies)

    if response.status_code != 200:
        raise RuntimeError(
            f"Could not load MyFitnessPal food search: HTTP {response.status_code}. "
            "The stored browser session may have expired."
        )

    raw_items = _extract_food_search_items(response.content, query)
    results = []
    for rank, wrapped_item in enumerate(raw_items[:limit], start=1):
        item = wrapped_item.get("item", {}) if isinstance(wrapped_item, dict) else {}
        if not item.get("id"):
            continue
        serving_sizes = item.get("serving_sizes") or []
        energy = (item.get("nutritional_contents") or {}).get("energy") or {}
        results.append(
            {
                "rank": rank,
                "name": item.get("description"),
                "exact_name_match": _food_name_key(item.get("description"))
                == _food_name_key(query),
                "brand": item.get("brand_name"),
                "serving": _format_serving_size(serving_sizes[0]) if serving_sizes else None,
                "available_servings": [
                    _format_serving_size(serving) for serving in serving_sizes[:5]
                ],
                **serving_capabilities(serving_sizes),
                "calories": energy.get("value"),
                "verified": bool(item.get("verified")),
                "nutrition_plausibility": assess_food_plausibility(item),
                "mfp_id": str(item["id"]),
            }
        )
    return results


def list_own_foods(client, search: str = "") -> list[dict[str, Any]]:
    """List the user's own custom foods (newest first), optionally filtered."""
    r = client.session.get(
        f"{MFP_WEB_BASE}/api/services/users/foods/mine",
        params={"search": search},
        headers=_web_headers(),
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Could not list own foods: HTTP {r.status_code}. "
            "The stored session may have expired — run refresh_browser_cookies."
        )
    return r.json() or []


def _serving_sizes(amount: float, unit: str) -> list[dict[str, Any]]:
    """Primary serving plus the container wrapper MFP's own client sends."""
    return [
        {
            "value": amount,
            "unit": unit,
            "nutrition_multiplier": 1,
            "gram_weight": 1,
            "fraction": False,
            "index": 0,
        },
        {
            "value": 1,
            "unit": f"container ({amount} {unit} ea.)",
            "nutrition_multiplier": 1,
            "gram_weight": 1,
            "fraction": False,
            "index": 1,
        },
    ]


def create_custom_food(client, spec: dict[str, Any]) -> dict[str, Any]:
    """
    Create a private custom food via the web BFF.

    Args:
        client: authenticated myfitnesspal.Client
        spec: validated CreateCustomFoodInput as a dict

    Returns:
        {"id": str|None, "status": int, "description": str}
    """
    csrf = _get_csrf_token(client)

    nutrition: dict[str, Any] = {
        "energy": {"unit": "calories", "value": spec["calories"]},
        "grams": 1,
    }
    for arg, api_key in _NUTRIENT_KEYS.items():
        value = spec.get(arg)
        if value is not None:
            nutrition[api_key] = value

    item = {
        "description": spec["description"],
        "brand_name": spec.get("brand_name") or "Generic",
        "public": bool(spec.get("public", False)),
        "type": "food",
        "nutritional_contents": nutrition,
        "serving_sizes": _serving_sizes(
            spec.get("serving_amount", 100), spec.get("serving_unit", "g")
        ),
    }
    item["country_code"] = spec.get("country_code") or "NL"
    # Ownership is assigned by MyFitnessPal from the session, so user_id is not
    # sent: posting without it returns 200 with the correct owner. Sending it
    # would also mean an extra request, and would fail for an account that has
    # no custom foods yet.

    r = client.session.post(
        f"{MFP_WEB_BASE}/api/services/foods",
        headers=_web_headers(csrf, json_body=True),
        data=json.dumps({"item": item}),
        timeout=30,
    )

    if r.status_code not in (200, 201):
        hint = "" if csrf else " (no CSRF token acquired)"
        detail = _api_error_detail(r)
        raise RuntimeError(
            f"Failed to create custom food: HTTP {r.status_code}{hint}"
            + (f" - {detail}" if detail else "")
        )

    # MFP returns a bare list of the created food object(s); older docs/clients
    # assumed {"item": {...}}. Accept both.
    new_id = None
    try:
        body = r.json()
        if isinstance(body, list) and body:
            new_id = body[0].get("id")
        elif isinstance(body, dict):
            new_id = (body.get("item") or body).get("id")
    except Exception:
        pass
    if new_id is None:
        logger.warning("Food created but MyFitnessPal returned no id")

    logger.info(f"Created custom food {new_id}: {spec['description']}")
    return {"id": new_id, "status": r.status_code, "description": spec["description"]}


def delete_custom_food(client, food_id: str) -> int:
    """Delete a custom food by id. MFP has no update endpoint — recreate + delete."""
    csrf = _get_csrf_token(client)
    r = client.session.delete(
        f"{MFP_WEB_BASE}/api/services/foods/{food_id}",
        headers=_web_headers(csrf),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        detail = _api_error_detail(r)
        raise RuntimeError(
            f"Failed to delete custom food {food_id}: HTTP {r.status_code}"
            + (f" - {detail}" if detail else "")
        )
    logger.info(f"Deleted custom food {food_id}")
    return r.status_code
