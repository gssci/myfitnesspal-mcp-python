"""Persistent MyFitnessPal session-cookie storage."""

import json
import logging
import time
from datetime import datetime, timedelta
from http.cookiejar import Cookie, CookieJar

from .config import CONFIG_DIR, COOKIES_FILE

logger = logging.getLogger("mfp_mcp")


def ensure_config_dir():
    """Ensure the config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)


def save_cookies(cookies: dict[str, str]):
    """
    Save session cookies to file for persistence.

    Args:
        cookies: Dictionary of cookie name -> value
    """
    ensure_config_dir()
    cookie_data = {
        "cookies": cookies,
        "saved_at": datetime.now().isoformat(),
    }
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookie_data, f, indent=2)
    # Session cookies grant full account access - restrict to owner only
    COOKIES_FILE.chmod(0o600)
    logger.info(f"Saved session cookies to {COOKIES_FILE}")


def load_cookies() -> dict[str, str] | None:
    """
    Load session cookies from file.

    Returns:
        Dictionary of cookies if file exists and is valid, None otherwise
    """
    if not COOKIES_FILE.exists():
        return None

    try:
        with open(COOKIES_FILE, "r") as f:
            cookie_data = json.load(f)

        # Check if cookies are less than 30 days old
        saved_at = datetime.fromisoformat(cookie_data.get("saved_at", "2000-01-01"))
        if datetime.now() - saved_at > timedelta(days=30):
            logger.info("Stored cookies are expired (>30 days old)")
            return None

        return cookie_data.get("cookies")
    except Exception as e:
        logger.warning(f"Failed to load cookies: {e}")
        return None


def dict_to_cookiejar(cookies_dict: dict[str, str], domain: str = ".myfitnesspal.com") -> CookieJar:
    """
    Convert a dictionary of cookies to a CookieJar that can be used by myfitnesspal.Client.

    Args:
        cookies_dict: Dictionary of cookie name -> value
        domain: Domain for the cookies (default: .myfitnesspal.com)

    Returns:
        CookieJar: A CookieJar object populated with the cookies
    """
    jar = CookieJar()

    for name, value in cookies_dict.items():
        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path="/",
            path_specified=True,
            secure=True,
            expires=int(time.time()) + 86400 * 30,  # 30 days from now
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None},
            rfc2109=False,
        )
        jar.set_cookie(cookie)

    return jar
