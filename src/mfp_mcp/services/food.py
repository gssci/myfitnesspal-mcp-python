"""Food lookup, search, and custom-food services."""

import json
import logging
import re
from datetime import date
from time import monotonic
from typing import Any
from urllib.parse import quote

from lxml import html as lxml_html

from ..config import (
    MFP_ADD_TO_DIARY_PAGE,
    MFP_API_BASE,
    MFP_BROWSER_USER_AGENT,
    MFP_FOOD_SEARCH_PAGE,
    MFP_LEGACY_SEARCH_PAGE,
    MFP_WEB_BASE,
)
from ..cookie_store import save_cookies
from ..units import (
    is_discrete_serving,
    is_gram_unit,
    normalize_unit,
    usable_gram_weight,
)
from .http import (
    MfpSessionExpiredError,
    _api_error_detail,
    _get_csrf_token,
    _mfp_api_headers,
    _web_headers,
    is_logged_out_response,
    refresh_session_from_browser,
)

logger = logging.getLogger("mfp_mcp")

_DIRECT_SERVING_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s+(.+?)\s*$")
_MAX_PLAUSIBLE_KCAL_PER_100 = 1000.0
_MEAL_FOOD_CACHE_TTL_SECONDS = 60
_V2_BATCH_SIZE = 25
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


def get_foods_v2(client, mfp_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch many foods in one round-trip, keyed by id.

    IDs MyFitnessPal no longer serves are simply absent from the result rather
    than raising, because search pages routinely list foods the v2 API has since
    dropped and one dead id must not lose the whole page of results.
    """
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(mfp_ids), _V2_BATCH_SIZE):
        batch = mfp_ids[start : start + _V2_BATCH_SIZE]
        response = client.session.get(
            f"{MFP_API_BASE}/v2/foods",
            params={"ids": ",".join(batch)},
            headers=_mfp_api_headers(client),
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Could not look up foods: HTTP {response.status_code}")
        for item in response.json().get("items") or []:
            if item.get("id") is not None:
                found[str(item["id"])] = item
    return found


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


def _dehydrated_queries(page_data: dict[str, Any]) -> list[Any] | None:
    """Pull React Query's dehydrated cache out of the Next.js page payload.

    Tolerates the two shapes MyFitnessPal has shipped: ``dehydratedState`` as
    ``{"queries": [...]}`` and as a bare list of queries.
    """
    props = page_data.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    state = page_props.get("dehydratedState") if isinstance(page_props, dict) else None
    if isinstance(state, dict):
        state = state.get("queries")
    return state if isinstance(state, list) else None


def _search_items_from_queries(queries: list[Any], query: str) -> list[Any] | None:
    """Find the cached result set for `query`, or the page's only one.

    The exact ``queryKey`` is matched on a normalised form, because the key
    MyFitnessPal echoes back is not always character-identical to what was
    requested (case, accents, and punctuation such as the ``%`` in
    "yogurt greco 0%" have all differed). When no key matches but the page
    carries exactly one food result set, that set is used: a rename of the
    query key should degrade search quality, not break search outright.
    """
    wanted = _food_name_key(query)
    candidates: list[list[Any]] = []
    for cached_query in queries:
        if not isinstance(cached_query, dict):
            continue
        data = (cached_query.get("state") or {}).get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        query_key = cached_query.get("queryKey") or []
        if (
            isinstance(query_key, list)
            and len(query_key) >= 2
            and str(query_key[0]) == "food"
            and _food_name_key(query_key[1]) == wanted
        ):
            return items
        candidates.append(items)

    if len(candidates) == 1:
        logger.warning(
            f"Search page held no query-cache entry keyed on {query!r}; "
            "falling back to the page's only food result set"
        )
        return candidates[0]
    return None


def _extract_food_search_items(content: bytes, query: str) -> list[dict[str, Any]]:
    """Extract food results from the search page's server-rendered state.

    Raises:
        MfpSessionExpiredError: If the page served is a logged-out route.
        RuntimeError: If the page is authenticated but no longer parseable.
    """
    document = lxml_html.fromstring(content)
    scripts = document.xpath("//script[@id='__NEXT_DATA__']/text()")
    if not scripts:
        raise RuntimeError(
            "MyFitnessPal search page contained no Next.js state. The page layout "
            "may have changed, or a bot-protection interstitial was served."
        )

    try:
        page_data = json.loads(scripts[0])
    except json.JSONDecodeError as e:
        raise RuntimeError("MyFitnessPal search page state was not valid JSON") from e
    if not isinstance(page_data, dict):
        raise RuntimeError("MyFitnessPal search page state was not an object")

    # A rejected session renders the logout/login route instead of the search
    # route. Without this check its state parses "successfully" into nothing and
    # an auth failure is reported as a layout change.
    route = str(page_data.get("page") or "")
    if any(marker in route for marker in ("/account/logout", "/account/login", "/welcome")):
        raise MfpSessionExpiredError("searching the food database")

    queries = _dehydrated_queries(page_data)
    if queries is None:
        raise RuntimeError(
            "MyFitnessPal search page state had an unexpected format: no dehydrated "
            "query cache under props.pageProps.dehydratedState. If this persists for "
            "every query, the search page layout has changed."
        )

    items = _search_items_from_queries(queries, query)
    if items is None:
        raise RuntimeError(
            f"MyFitnessPal search page contained no results state for {query!r} "
            f"(the page cached {len(queries)} queries, none of them food results)."
        )
    return items


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


def _parse_history_fragment(content: bytes, source: str | None = None) -> list[dict[str, Any]]:
    """Parse recent/frequent-food rows out of classic MyFitnessPal markup.

    Handles both the XHR fragments (one list per response, `source` given) and
    the add-to-diary page (several lists in one document, `source` taken from
    each row's `data-list-type`).
    """
    if not (content or b"").strip():
        return []
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
            # Rounded because MyFitnessPal stores these as float32: an unrounded
            # 0.8 prints as 0.800000011920929, which is a dozen wasted tokens on
            # every row of a list the model reads in full.
            previous_quantity: float | None = round(float(str(quantity).replace(",", ".")), 4)
        except (TypeError, ValueError):
            previous_quantity = None
        results.append(
            {
                "history_id": str(history_id),
                "name": " ".join(cells[1].text_content().split()),
                "source": source or checkbox.get("data-list-type") or "frequent",
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


_HISTORY_FRAGMENT_MARKERS = (b"favorites[", b'class="favorite"', b"data-food-id")


def _looks_like_history_fragment(response) -> bool:
    """True when a classic history endpoint answered with a real HTML fragment.

    The endpoints answer an expired session with HTTP 200 after redirecting to
    a full login page, so an empty parse is ambiguous: it means either "this
    meal has no history" or "you are logged out".

    Anything carrying history markup is accepted outright, so a change to the
    fragment's wrapper can never be misreported as an auth failure. Only when
    there is no history markup at all does the ``<html>`` document wrapper
    decide: a genuine fragment is a bare partial, a login page is a document.
    """
    if is_logged_out_response(response):
        return False
    body = response.content or b""
    if any(marker in body for marker in _HISTORY_FRAGMENT_MARKERS):
        return True
    return b"<html" not in body[:8192].lower()


def _post_meal_history(client, meal: int, endpoint: str):
    """POST one classic recent/frequent request for a meal."""
    return client.session.post(
        f"{MFP_WEB_BASE}/food/{endpoint}",
        data={"meal": meal, "base_index": 0, "page": 1},
        headers={
            "Accept": "text/html, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": MFP_BROWSER_USER_AGENT,
            "Referer": f"{MFP_WEB_BASE}/food/diary",
        },
        timeout=30,
    )


def _meal_foods_from_add_page(client, meal: int) -> dict[str, list[dict[str, Any]]]:
    """Read the history lists the add-to-diary page renders server-side.

    `/food/add_to_diary?meal=N` is a plain Rails GET, so it survives the
    rejected-NextAuth state that bounces the XHR fragments. It carries fewer
    lists than the fragments do (typically frequent only, since the Recent tab
    is filled in by script), which is still far better than the empty history
    that used to send the agent off to search every food from scratch.
    """
    response = client.session.get(
        MFP_ADD_TO_DIARY_PAGE,
        params={"meal": meal},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": MFP_BROWSER_USER_AGENT,
        },
        timeout=30,
    )
    if response.status_code != 200 or is_logged_out_response(response):
        return {}
    lists: dict[str, list[dict[str, Any]]] = {}
    for item in _parse_history_fragment(response.content):
        lists.setdefault(item["source"], []).append(item)
    return lists


def get_meal_foods(client, meal: int) -> dict[str, Any]:
    """Return the account's recent and frequent foods for one meal.

    Returns `recent` and `frequent` lists, plus `degraded` when a list is
    missing because MyFitnessPal refused its request rather than because the
    account has no such history.

    Raises:
        MfpSessionExpiredError: If no source is reachable. Reporting a rejected
            session as an empty history is what made callers fall back to
            searching every food from scratch.
    """
    cache_key = (id(client), meal)
    cached = _MEAL_FOOD_CACHE.get(cache_key)
    if cached and monotonic() - cached[0] <= _MEAL_FOOD_CACHE_TTL_SECONDS:
        return cached[1]

    output: dict[str, Any] = {"recent": [], "frequent": []}
    refresh_attempted = False
    bounced = False
    for source, endpoint in (("recent", "load_recent"), ("frequent", "load_most_used")):
        response = _post_meal_history(client, meal, endpoint)
        if response.status_code != 200:
            raise RuntimeError(f"Could not load {source} foods: HTTP {response.status_code}")

        if not _looks_like_history_fragment(response):
            if not refresh_attempted:
                refresh_attempted = True
                browser_cookies = refresh_session_from_browser(client)
                if browser_cookies:
                    response = _post_meal_history(client, meal, endpoint)
                    if response.status_code == 200 and _looks_like_history_fragment(response):
                        save_cookies(browser_cookies)
            if not _looks_like_history_fragment(response):
                bounced = True
                continue

        output[source] = _parse_history_fragment(response.content, source)

    if bounced:
        # These fragments are POSTs, and MyFitnessPal rejects POSTs whenever the
        # NextAuth session is gone even though the legacy one still works. Fall
        # back to the server-rendered page before declaring the history empty.
        for source, items in _meal_foods_from_add_page(client, meal).items():
            if not output.get(source):
                output[source] = items
        if not output["recent"] and not output["frequent"]:
            raise MfpSessionExpiredError("reading recent and frequent meal foods")
        output["degraded"] = True
        logger.warning(
            "MyFitnessPal rejected the recent/frequent food requests; "
            "served the add-to-diary page's lists instead"
        )

    # Never cache an empty history: it is cheap to re-read, and caching it used
    # to hide a freshly repaired session for a further 60 seconds.
    if output["recent"] or output["frequent"]:
        _MEAL_FOOD_CACHE[cache_key] = (monotonic(), output)
    return output


def invalidate_meal_food_cache(client) -> None:
    """Discard cached recent/frequent lists after a diary mutation."""
    client_id = id(client)
    for key in [key for key in _MEAL_FOOD_CACHE if key[0] == client_id]:
        del _MEAL_FOOD_CACHE[key]


def _resolve_history_id_from_search(client, history_id: str, name: str, meal: int) -> str | None:
    """Bridge a classic history ID to the modern numeric v2 food ID.

    History rows expose only the classic food ID, which the v2 API does not
    accept, and the only public mapping is the classic search listing, where
    each hit carries both (`data-original-id` and `data-external-id`).

    Searched over GET: the site posts this form, but MyFitnessPal bounces the
    POST to /account/login whenever the NextAuth session has been rejected —
    which is exactly the state in which meal history matters most.
    """
    response = client.session.get(
        MFP_LEGACY_SEARCH_PAGE,
        params={"search": name, "date": date.today().isoformat(), "meal": str(meal)},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": MFP_BROWSER_USER_AGENT,
        },
        timeout=30,
    )
    if is_logged_out_response(response):
        raise MfpSessionExpiredError("resolving a food from meal history")
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
        # Older diary entries point at food records MyFitnessPal has since
        # dropped from its search index, so no query rewriting brings them back.
        # Hand over a usable query instead: history names are "Brand -
        # Description", which searches poorly as a single string.
        return {
            "resolved": False,
            "reason": "modern_id_not_found",
            "name": candidate["name"],
            "suggested_query": candidate["name"].split(" - ")[-1],
            "fallback": "Use mfp_search_food with suggested_query and validate the result.",
        }
    food = get_food_v2(client, mfp_id)
    energy = ((food.get("nutritional_contents") or {}).get("energy") or {}).get("value")
    serving_sizes = food.get("serving_sizes") or []
    capabilities = serving_capabilities(serving_sizes)
    # `servings` and `serving_options` used to be reported side by side, which
    # spent tokens printing the same serving table twice. One formatted list,
    # spelled the same way search results spell it, says all of it.
    result: dict[str, Any] = {
        "resolved": True,
        "mfp_id": str(mfp_id),
        "name": food.get("description") or candidate["name"],
        "brand": food.get("brand_name"),
        "units": [_format_serving_size(s) for s in serving_sizes[:5]],
        "calories": energy,
    }
    if capabilities["count_units"]:
        result["whole_item_units"] = capabilities["count_units"]
    if assess_food_plausibility(food)["status"] == "implausible":
        result["implausible"] = True
    return result


def _result_from_v2_item(item: dict[str, Any], rank: int, query: str) -> dict[str, Any]:
    """Shape one food record the way the search tools report it.

    Deliberately lean. This list is read in full by the model and then carried
    for the rest of the request, so every field has to earn its tokens. Dropped
    since it did not: ``rank`` (the list is already in rank order), ``serving``
    and ``available_servings`` (both restate ``units``), ``supports_grams``
    (true for nearly every food, so it never discriminates), ``supports_count``
    (implied by ``whole_item_units``), and ``verified`` (nothing reads it).
    Flags that only matter when set are emitted only when set.
    """
    del rank
    serving_sizes = item.get("serving_sizes") or []
    energy = (item.get("nutritional_contents") or {}).get("energy") or {}
    capabilities = serving_capabilities(serving_sizes)
    result: dict[str, Any] = {
        "name": item.get("description"),
        "brand": item.get("brand_name"),
        "units": [_format_serving_size(serving) for serving in serving_sizes[:5]],
        "calories": energy.get("value"),
        "mfp_id": str(item["id"]),
    }
    if _food_name_key(item.get("description")) == _food_name_key(query):
        result["exact_name_match"] = True
    if capabilities["count_units"]:
        result["whole_item_units"] = capabilities["count_units"]
    if assess_food_plausibility(item)["status"] == "implausible":
        result["implausible"] = True
    return result


def search_foods_legacy(client, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search foods through the classic Rails search page, returning v2 records.

    Returns the raw v2 food records rather than reported results, so callers
    that need the full serving table (mfp_log_food, which has to verify a unit
    before committing an entry) do not have to re-fetch every candidate.

    Preferred over the Next.js page because it authenticates off `_mfp_session`
    alone: it keeps answering when the NextAuth session has been rejected, which
    is the state that turned every search into "search page state had an
    unexpected format" and made the agent conclude the food did not exist.

    Note the GET form. The site's own search posts a form, but MyFitnessPal
    bounces unauthenticated-by-NextAuth POSTs to /account/login while serving
    the identical GET; the query string is all the Rails action reads.

    Each result anchor carries `data-external-id`, which *is* the v2 food id, so
    the listing is enriched from the v2 API in a single batch rather than
    scraped — that yields real serving sizes instead of the one-line summary the
    page prints.
    """
    response = client.session.get(
        MFP_LEGACY_SEARCH_PAGE,
        params={"search": query, "meal": 0, "date": date.today().isoformat()},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": MFP_BROWSER_USER_AGENT,
        },
        timeout=30,
    )
    if is_logged_out_response(response):
        raise MfpSessionExpiredError("searching the food database")
    if response.status_code != 200:
        raise RuntimeError(f"Could not load the classic food search: HTTP {response.status_code}")

    document = lxml_html.fromstring(response.content)
    ids: list[str] = []
    for anchor in document.xpath("//a[@data-external-id]"):
        food_id = str(anchor.get("data-external-id") or "")
        if food_id and food_id not in ids:
            ids.append(food_id)
        if len(ids) >= limit:
            break
    if not ids:
        return []

    items = get_foods_v2(client, ids)
    # Ids listed by search but no longer served by the v2 API are skipped: they
    # could not be added to the diary anyway.
    return [items[food_id] for food_id in ids if food_id in items]


def search_food_records(client, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search the MyFitnessPal food database, returning raw v2 food records.

    Tries the classic page first and falls back to the Next.js one, so a search
    only fails when *both* of MyFitnessPal's sessions are dead.
    """
    try:
        records = search_foods_legacy(client, query, limit)
        if records:
            return records
        logger.info(f"Classic search returned nothing for {query!r}; trying the Next.js page")
    except Exception as e:
        # The two surfaces authenticate off different sessions, so the classic
        # one being rejected says nothing about the other. Report the classic
        # failure only if the Next.js page cannot answer either.
        logger.warning(f"Classic food search failed ({e}); falling back to the Next.js page")
        try:
            return search_foods_next(client, query, limit)
        except Exception:
            raise e from None
    return search_foods_next(client, query, limit)


def search_foods_web(client, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search the MyFitnessPal food database, shaped for reporting to a model."""
    return [
        _result_from_v2_item(item, rank, query)
        for rank, item in enumerate(search_food_records(client, query, limit), start=1)
    ]


def search_foods_next(client, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search foods through the Next.js page's state, returning v2 records."""
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
    if is_logged_out_response(response):
        browser_cookies = refresh_session_from_browser(client)
        if browser_cookies:
            response = load_page()
            if response.status_code == 200 and not is_logged_out_response(response):
                save_cookies(browser_cookies)
                # The stale session's recent/frequent lists were read as empty.
                invalidate_meal_food_cache(client)
        # Bail out rather than parse the logout page: its Next.js state has no
        # dehydrated query cache, which previously surfaced as a bogus
        # "search page state had an unexpected format" error and led callers to
        # conclude the food did not exist.
        if is_logged_out_response(response):
            raise MfpSessionExpiredError("searching the food database")

    if response.status_code != 200:
        raise RuntimeError(
            f"Could not load MyFitnessPal food search: HTTP {response.status_code}. "
            "The stored browser session may have expired."
        )

    raw_items = _extract_food_search_items(response.content, query)
    records = []
    for wrapped_item in raw_items[:limit]:
        item = wrapped_item.get("item", {}) if isinstance(wrapped_item, dict) else {}
        if not item.get("id"):
            continue
        records.append(item)
    return records


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
