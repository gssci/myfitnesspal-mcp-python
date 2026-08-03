"""Chromium cookie extraction: host collisions, profiles, and session validation."""

import sqlite3

import pytest

from mfp_mcp import browser_cookies


def _cookie_db(tmp_path, rows):
    path = tmp_path / "Cookies"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies (name TEXT, value TEXT, encrypted_value BLOB, "
        "host_key TEXT, last_update_utc INTEGER)"
    )
    con.executemany("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


TOKEN = "__Secure-next-auth.session-token"


def test_www_row_wins_over_the_api_row(tmp_path):
    """Both hosts issue this cookie, and only the www value authenticates the site.

    Collapsing rows by name alone let SQLite's row order decide, which is why
    the same code intermittently bounced to /account/logout.
    """
    db = _cookie_db(
        tmp_path,
        [
            (TOKEN, "api-token", None, "api.myfitnesspal.com", 200),
            (TOKEN, "www-token", None, "www.myfitnesspal.com", 100),
        ],
    )

    cookies = browser_cookies._extract_chromium_cookies_macos(db, aes_key=b"")

    assert cookies[TOKEN] == "www-token"


def test_the_newer_row_wins_within_the_same_host(tmp_path):
    db = _cookie_db(
        tmp_path,
        [
            (TOKEN, "old", None, "www.myfitnesspal.com", 100),
            (TOKEN, "new", None, "www.myfitnesspal.com", 200),
        ],
    )

    cookies = browser_cookies._extract_chromium_cookies_macos(db, aes_key=b"")

    assert cookies[TOKEN] == "new"


def test_every_profile_is_searched(tmp_path, monkeypatch):
    """A login in "Profile 1" used to be invisible: only Default was read."""
    appsup = tmp_path / "Library" / "Application Support"
    for profile in ("Default", "Profile 1"):
        (appsup / "Google/Chrome" / profile).mkdir(parents=True)
        (appsup / "Google/Chrome" / profile / "Cookies").touch()
    monkeypatch.setattr(browser_cookies.Path, "home", classmethod(lambda cls: tmp_path))

    found = {path.parent.name for path in browser_cookies._chromium_cookie_dbs("Chrome")}

    assert found == {"Default", "Profile 1"}


class _Discovery:
    """Two browsers, only the second of which MyFitnessPal still accepts."""

    def __init__(self, monkeypatch):
        monkeypatch.setattr(
            browser_cookies,
            "_list_chromium_safe_storage_services_macos",
            lambda: ["Chrome Safe Storage", "Arc Safe Storage"],
        )
        monkeypatch.setattr(
            browser_cookies,
            "_chromium_profile_cookie_sets",
            lambda service: [(service.split()[0], {"_mfp_session": service.split()[0]})],
        )
        monkeypatch.setattr(browser_cookies.sys, "platform", "darwin")


def test_discovery_skips_a_browser_whose_session_is_rejected(monkeypatch):
    _Discovery(monkeypatch)

    result = browser_cookies.try_chromium_browsers_for_session_cookies(
        validate=lambda cookies: cookies["_mfp_session"] == "Arc"
    )

    assert result == ("Arc", {"_mfp_session": "Arc"})


def test_discovery_falls_back_when_no_session_validates(monkeypatch):
    """Legacy-only surfaces still work off a rejected web session, so keep one."""
    _Discovery(monkeypatch)

    result = browser_cookies.try_chromium_browsers_for_session_cookies(
        validate=lambda cookies: False
    )

    assert result == ("Chrome", {"_mfp_session": "Chrome"})


@pytest.mark.parametrize(
    ("host", "rank"),
    [
        ("www.myfitnesspal.com", 3),
        (".myfitnesspal.com", 2),
        ("api.myfitnesspal.com", 0),
    ],
)
def test_host_rank_prefers_the_site_we_talk_to(host, rank):
    assert browser_cookies._host_rank(host) == rank
