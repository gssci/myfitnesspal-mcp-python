"""Expired-session handling for food search and meal history.

MyFitnessPal answers a rejected session with HTTP 200 on a logged-out page,
so both surfaces used to report an auth failure as data: search raised a
"page state had an unexpected format" error, and meal history returned an
empty list. Callers then concluded the food did not exist and searched the
whole database from scratch.
"""

import json

import pytest

from mfp_mcp.services import food as food_service
from mfp_mcp.services.http import MfpSessionExpiredError

SEARCH_URL = "https://www.myfitnesspal.com/food/calorie-chart-nutrition-facts/banana"
LEGACY_SEARCH_URL = "https://www.myfitnesspal.com/food/search"
ADD_TO_DIARY_URL = "https://www.myfitnesspal.com/food/add_to_diary"
LOGOUT_URL = "https://www.myfitnesspal.com/account/logout"
LOGIN_URL = "https://www.myfitnesspal.com/account/login"

_EMPTY_HTML = b"<html><body></body></html>"


def _next_data(payload):
    return (
        "<html><body><script id='__NEXT_DATA__' type='application/json'>"
        + json.dumps(payload)
        + "</script></body></html>"
    ).encode()


def _results_page(query="banana", key=None, items=None):
    return _next_data(
        {
            "page": "/food/calorie-chart-nutrition-facts/[query]",
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "queryKey": ["food", key if key is not None else query, 1],
                                "state": {"data": {"items": items or []}},
                            }
                        ]
                    }
                }
            },
        }
    )


def _logout_page():
    """What MyFitnessPal actually renders once NextAuth rejects the session."""
    return _next_data({"page": "/account/logout", "props": {"pageProps": {}}})


_BANANA = {
    "item": {
        "id": "1234",
        "description": "Banana",
        "serving_sizes": [{"value": 100, "unit": "g", "nutrition_multiplier": 1}],
        "nutritional_contents": {"energy": {"value": 89}},
    }
}


class _Response:
    def __init__(self, content=_EMPTY_HTML, url=SEARCH_URL, status_code=200):
        self.content = content
        self.url = url
        self.status_code = status_code


class _Session:
    """Serves a queued response per request and records what was asked for.

    The classic (Rails) surfaces have their own queue, because search and meal
    history now try them before the Next.js page. Their default is a page with
    no results, so a test that says nothing about them exercises the Next.js
    path exactly as it did before those fallbacks existed.
    """

    def __init__(self, responses, legacy=None):
        self._responses = list(responses)
        self._legacy = list(legacy or [])
        self.requests = []
        self.cookies = _CookieJar()

    def _next(self, url):
        self.requests.append(url)
        if url.startswith((LEGACY_SEARCH_URL, ADD_TO_DIARY_URL)):
            return self._legacy.pop(0) if self._legacy else _Response(_EMPTY_HTML, url=url)
        return self._responses.pop(0) if self._responses else _Response()

    def get(self, url, **kwargs):
        return self._next(url)

    def post(self, url, **kwargs):
        return self._next(url)


class _CookieJar:
    def __init__(self):
        self.cleared = 0
        self.updated = 0

    def clear(self):
        self.cleared += 1

    def update(self, other):
        self.updated += 1


class _Client:
    def __init__(self, responses, legacy=None):
        self.session = _Session(responses, legacy)


@pytest.fixture(autouse=True)
def _clear_meal_cache():
    food_service._MEAL_FOOD_CACHE.clear()
    yield
    food_service._MEAL_FOOD_CACHE.clear()


@pytest.fixture
def refresh(monkeypatch):
    """Control the browser-cookie refresh and record whether cookies were kept."""

    saved = []
    monkeypatch.setattr(food_service, "save_cookies", saved.append)

    def configure(cookies):
        calls = []

        def fake_refresh(client):
            calls.append(client)
            return cookies

        monkeypatch.setattr(food_service, "refresh_session_from_browser", fake_refresh)
        return calls

    configure.saved = saved
    return configure


# --- search -----------------------------------------------------------------


def test_search_reports_expired_session_instead_of_a_parse_error(refresh):
    """A logout page must never be parsed as an empty/odd result set."""
    refresh(None)
    client = _Client([_Response(_logout_page(), url=LOGOUT_URL)])

    with pytest.raises(MfpSessionExpiredError) as excinfo:
        food_service.search_foods_web(client, "banana")

    assert "Authentication error" in str(excinfo.value)
    assert "unexpected format" not in str(excinfo.value)


def test_search_recovers_when_the_browser_has_a_live_session(refresh):
    calls = refresh({"__Secure-next-auth.session-token": "fresh"})
    client = _Client(
        [
            _Response(_logout_page(), url=LOGOUT_URL),
            _Response(_results_page(items=[_BANANA])),
        ]
    )

    results = food_service.search_foods_web(client, "banana")

    assert [result["name"] for result in results] == ["Banana"]
    assert len(calls) == 1
    assert refresh.saved == [{"__Secure-next-auth.session-token": "fresh"}]


def test_search_does_not_persist_cookies_that_still_fail(refresh):
    refresh({"__Secure-next-auth.session-token": "also-stale"})
    client = _Client(
        [
            _Response(_logout_page(), url=LOGOUT_URL),
            _Response(_logout_page(), url=LOGOUT_URL),
        ]
    )

    with pytest.raises(MfpSessionExpiredError):
        food_service.search_foods_web(client, "banana")

    assert refresh.saved == []


def test_search_detects_a_logout_page_served_without_a_redirect():
    """Some logout responses keep the requested URL; the route still gives it away."""
    client = _Client([_Response(_logout_page(), url=SEARCH_URL)])

    with pytest.raises(MfpSessionExpiredError):
        food_service.search_foods_web(client, "banana")


def test_search_matches_a_query_key_that_differs_only_in_normalisation():
    """MFP echoes keys back with different case and punctuation."""
    client = _Client([_Response(_results_page(key="Yogurt Greco 0%", items=[_BANANA]))])

    results = food_service.search_foods_web(client, "yogurt greco 0%")

    assert len(results) == 1


def test_search_falls_back_to_the_pages_only_result_set():
    """A renamed query key should degrade ranking, not break search."""
    client = _Client([_Response(_results_page(key="renamed-key", items=[_BANANA]))])

    results = food_service.search_foods_web(client, "banana")

    assert [result["name"] for result in results] == ["Banana"]


def test_search_still_reports_a_genuinely_unparseable_page():
    client = _Client([_Response(_next_data({"page": "/food", "props": {"pageProps": {}}}))])

    with pytest.raises(RuntimeError) as excinfo:
        food_service.search_foods_web(client, "banana")

    assert "dehydrated query cache" in str(excinfo.value)
    assert not isinstance(excinfo.value, MfpSessionExpiredError)


def test_search_returns_zero_results_without_erroring():
    client = _Client([_Response(_results_page(items=[]))])

    assert food_service.search_foods_web(client, "banana") == []


def _legacy_results_page(*ids):
    anchors = "".join(
        f'<li><a class="search" data-external-id="{external}" '
        f'data-original-id="{original}">food</a></li>'
        for external, original in ids
    )
    return f"<html><body><ul>{anchors}</ul></body></html>".encode()


def test_search_prefers_the_classic_page_and_enriches_it_from_the_api(monkeypatch):
    """The classic page survives a rejected NextAuth session; the Next.js one does not."""
    monkeypatch.setattr(
        food_service,
        "get_foods_v2",
        lambda client, ids: {
            "1234": {
                "id": "1234",
                "description": "Banana",
                "serving_sizes": [{"value": 118, "unit": "g", "nutrition_multiplier": 1}],
                "nutritional_contents": {"energy": {"value": 105}},
            }
        },
    )
    client = _Client([], legacy=[_Response(_legacy_results_page(("1234", "99")))])

    results = food_service.search_foods_web(client, "banana")

    assert [result["mfp_id"] for result in results] == ["1234"]
    assert results[0]["serving"] == "118 g"
    # The Next.js page was never requested.
    assert all(url.startswith(LEGACY_SEARCH_URL) for url in client.session.requests)


def test_search_drops_results_the_api_no_longer_serves(monkeypatch):
    """A food listed by search but gone from the API cannot be logged anyway."""
    monkeypatch.setattr(food_service, "get_foods_v2", lambda client, ids: {})
    client = _Client(
        [_Response(_results_page(items=[_BANANA]))],
        legacy=[_Response(_legacy_results_page(("1234", "99")))],
    )

    results = food_service.search_foods_web(client, "banana")

    # Nothing survived enrichment, so the Next.js page answered instead.
    assert [result["name"] for result in results] == ["Banana"]


def test_search_falls_back_to_next_js_when_the_classic_page_bounces():
    """Only the legacy session is dead here, so the Next.js page still answers."""
    client = _Client(
        [_Response(_results_page(items=[_BANANA]))],
        legacy=[_Response(_EMPTY_HTML, url=LOGIN_URL)],
    )

    assert [result["name"] for result in food_service.search_foods_web(client, "banana")] == [
        "Banana"
    ]


def test_search_reports_the_classic_failure_when_neither_surface_answers(refresh):
    refresh(None)
    client = _Client(
        [_Response(_logout_page(), url=LOGOUT_URL)],
        legacy=[_Response(_EMPTY_HTML, url=LOGIN_URL)],
    )

    with pytest.raises(MfpSessionExpiredError):
        food_service.search_foods_web(client, "banana")


# --- meal history -----------------------------------------------------------


_FRAGMENT = b"""
<div id="recent_page_1"><table><tr class="favorite">
  <td><input class="checkbox" data-food-id="303610008" data-food-verified="false" /></td>
  <td>Banana</td>
  <td><input name="favorites[0][quantity]" value="1" />
      <select name="favorites[0][weight_id]">
        <option value="1" selected="selected">100 g</option>
      </select></td>
</tr></table></div>
"""

_EMPTY_FRAGMENT = b'<div id="recent_page_1"><table></table></div>'

_LOGIN_PAGE = b"<html><body><form action='/account/login'>Log in</form></body></html>"


def test_meal_foods_reports_expired_session_instead_of_an_empty_history(refresh):
    """The bug behind 'recent_count: 0' on an account with plenty of history."""
    refresh(None)
    client = _Client([_Response(_LOGIN_PAGE, url=LOGIN_URL)])

    with pytest.raises(MfpSessionExpiredError) as excinfo:
        food_service.get_meal_foods(client, meal=2)

    assert "Authentication error" in str(excinfo.value)


def test_meal_foods_recovers_by_refreshing_the_browser_session(refresh):
    calls = refresh({"__Secure-next-auth.session-token": "fresh"})
    client = _Client(
        [
            _Response(_LOGIN_PAGE, url=LOGIN_URL),  # recent, logged out
            _Response(_FRAGMENT),  # recent, after refresh
            _Response(_FRAGMENT),  # frequent
        ]
    )

    result = food_service.get_meal_foods(client, meal=2)

    assert len(result["recent"]) == 1
    assert len(result["frequent"]) == 1
    assert len(calls) == 1
    assert refresh.saved == [{"__Secure-next-auth.session-token": "fresh"}]


def test_meal_foods_accepts_a_genuinely_empty_history():
    client = _Client([_Response(_EMPTY_FRAGMENT), _Response(_EMPTY_FRAGMENT)])

    result = food_service.get_meal_foods(client, meal=0)

    assert result == {"recent": [], "frequent": []}


def test_meal_foods_does_not_cache_an_empty_history():
    """Caching emptiness used to mask a session repaired seconds later."""
    client = _Client(
        [
            _Response(_EMPTY_FRAGMENT),
            _Response(_EMPTY_FRAGMENT),
            _Response(_FRAGMENT),
            _Response(_FRAGMENT),
        ]
    )

    assert food_service.get_meal_foods(client, meal=0) == {"recent": [], "frequent": []}
    assert len(food_service.get_meal_foods(client, meal=0)["recent"]) == 1
    assert len(client.session.requests) == 4


_ADD_TO_DIARY_PAGE = b"""
<html><body><table>
<tr class="favorite">
  <td><input class="checkbox" data-food-id="42" data-list-type="frequent" /></td>
  <td>Fette biscottate integrali</td>
  <td><input name="favorites[0][quantity]" value="2" />
      <select name="favorites[0][weight_id]">
        <option value="1" selected="selected">1 fetta</option>
      </select></td>
</tr></table></body></html>
"""


def test_meal_foods_falls_back_to_the_add_to_diary_page(refresh):
    """The fragments are POSTs, which MyFitnessPal bounces on their own schedule."""
    refresh(None)
    client = _Client(
        [_Response(_LOGIN_PAGE, url=LOGIN_URL), _Response(_LOGIN_PAGE, url=LOGIN_URL)],
        legacy=[_Response(_ADD_TO_DIARY_PAGE, url=ADD_TO_DIARY_URL)],
    )

    result = food_service.get_meal_foods(client, meal=2)

    assert [item["name"] for item in result["frequent"]] == ["Fette biscottate integrali"]
    assert result["recent"] == []
    # The empty `recent` here means "refused", not "the account has none".
    assert result["degraded"] is True
    assert client.session.requests[-1].startswith(ADD_TO_DIARY_URL)


def test_meal_foods_keeps_the_fragment_lists_it_did_get(refresh):
    refresh(None)
    client = _Client(
        [_Response(_FRAGMENT), _Response(_LOGIN_PAGE, url=LOGIN_URL)],
        legacy=[_Response(_ADD_TO_DIARY_PAGE, url=ADD_TO_DIARY_URL)],
    )

    result = food_service.get_meal_foods(client, meal=2)

    assert [item["name"] for item in result["recent"]] == ["Banana"]
    assert [item["name"] for item in result["frequent"]] == ["Fette biscottate integrali"]


def test_meal_foods_sends_the_meal_and_ajax_headers():
    client = _Client([_Response(_FRAGMENT), _Response(_FRAGMENT)])

    food_service.get_meal_foods(client, meal=3)

    assert [url.rsplit("/", 1)[-1] for url in client.session.requests] == [
        "load_recent",
        "load_most_used",
    ]
