"""Chromium cookie discovery and decryption on macOS."""

import hashlib
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("mfp_mcp")

_CHROMIUM_COOKIES_PATHS_MACOS: dict[str, list[str]] = {
    "Arc": ["Arc/User Data/Default/Network/Cookies", "Arc/User Data/Default/Cookies"],
    "Chrome": ["Google/Chrome/Default/Network/Cookies", "Google/Chrome/Default/Cookies"],
    "Chromium": ["Chromium/Default/Network/Cookies", "Chromium/Default/Cookies"],
    "Microsoft Edge": ["Microsoft Edge/Default/Network/Cookies", "Microsoft Edge/Default/Cookies"],
    "Brave": [
        "BraveSoftware/Brave-Browser/Default/Network/Cookies",
        "BraveSoftware/Brave-Browser/Default/Cookies",
    ],
    "Vivaldi": ["Vivaldi/Default/Network/Cookies", "Vivaldi/Default/Cookies"],
    "Opera": ["com.operasoftware.Opera/Network/Cookies", "com.operasoftware.Opera/Cookies"],
}

_CHROMIUM_BROWSER_ALIASES: dict[str, str] = {
    "arc": "Arc",
    "chrome": "Chrome",
    "chromium": "Chromium",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
}


def _safe_storage_keychain_password(service_name: str) -> bytes | None:
    """Look up `service_name` in the macOS Keychain and return the raw bytes.

    Returns None if the entry doesn't exist or access is denied.

    NOTE: on a fresh install the first read of another app's Safe Storage
    entry triggers a macOS keychain authorization dialog ("<app> wants to
    use information stored in your keychain"). If this MCP is running
    headless (e.g. spawned by Claude Desktop with no UI focus), the prompt
    is silently denied and this call returns None after the 5s timeout.
    The user only needs to click "Always Allow" once, but they need to
    know to look for the prompt — the README troubleshooting section
    documents this.
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service_name, "-w"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _list_chromium_safe_storage_services_macos() -> list[str]:
    """Return all keychain service names ending in 'Safe Storage'.

    These identify installed Chromium-based browsers. We don't hard-code
    the list — anything matching the pattern is fair game.
    """
    keychain_path = os.path.expanduser("~/Library/Keychains/login.keychain-db")
    try:
        result = subprocess.run(
            ["security", "dump-keychain", keychain_path],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []
    services = set()
    text = result.stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        # The `svce` attribute appears as: "svce"<blob>="Arc Safe Storage"
        if '"svce"<blob>=' not in line or "Safe Storage" not in line:
            continue
        try:
            value = line.split('"svce"<blob>=', 1)[1].strip()
            value = value.strip('"')
            if value.endswith("Safe Storage"):
                services.add(value)
        except IndexError:
            continue
    return sorted(services)


def _derive_chromium_aes_key_macos(safe_storage_password: bytes) -> bytes:
    """Derive the AES-128 cookie key Chromium uses on macOS.

    Per Chromium's `os_crypt_mac.mm`: PBKDF2-HMAC-SHA1 with salt='saltysalt',
    1003 iterations, 16-byte key.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1003,
        backend=default_backend(),
    )
    return kdf.derive(safe_storage_password)


def _decrypt_chromium_value_macos(
    encrypted_value: bytes, aes_key: bytes, host_key: str = ""
) -> str | None:
    """Decrypt a single Chromium cookie `encrypted_value`. Returns None on
    failure or for unsupported schemes (e.g. v20 app-bound encryption).

    `host_key` is the cookie's host column from SQLite; modern Chromium
    prepends `SHA-256(host_key)` to the plaintext as an integrity tag, so
    we strip exactly that 32-byte prefix when it's present. Without this
    check, long ASCII cookie values from legacy rows would be silently
    truncated by 32 bytes (the shortened plaintext still decodes as UTF-8).
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not encrypted_value or len(encrypted_value) < 3:
        return None
    prefix = encrypted_value[:3]
    if prefix not in (b"v10", b"v11"):
        # v20 needs app-bound decryption via the browser process and is not
        # supported here. Caller should fall back to a different source.
        return None
    try:
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.CBC(b" " * 16),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
    except Exception:
        return None
    # Strip PKCS#7 padding.
    if not plaintext:
        return None
    pad_len = plaintext[-1]
    if pad_len < 1 or pad_len > 16:
        return None
    plaintext = plaintext[:-pad_len]
    # Strip the SHA-256(host_key) integrity prefix only when it actually
    # matches — never blindly. Legacy rows without the prefix have shorter
    # but otherwise normal plaintexts.
    if host_key and len(plaintext) >= 32:
        expected_prefix = hashlib.sha256(host_key.encode("utf-8")).digest()
        if plaintext[:32] == expected_prefix:
            plaintext = plaintext[32:]
    try:
        return plaintext.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _snapshot_sqlite_db(src: Path, dst: str) -> None:
    """Copy a live SQLite DB into `dst` using the backup API.

    The browser's cookies DB may be open in WAL mode with active writers;
    a plain `shutil.copy` misses committed rows that still live in the
    `-wal` sidecar. The backup API handles WAL/SHM correctly, takes a
    consistent snapshot, and doesn't require taking a write lock — opening
    the source read-only is enough.
    """
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def _extract_chromium_cookies_macos(
    cookies_db_path: Path,
    aes_key: bytes,
    domain: str = "myfitnesspal.com",
) -> dict[str, str]:
    """Read cookies for `domain` (and its subdomains) from a Chromium DB.

    The DB is snapshotted via the SQLite backup API so rows pending in the
    `-wal` file are included. Cookies whose decrypted value isn't clean
    UTF-8 are skipped — those can't go into HTTP headers anyway.
    """
    # `mkstemp` gives us a uniquely-named file we own, immune to the
    # time-of-check/time-of-use race that `mktemp` would create.
    fd, tmp_path = tempfile.mkstemp(suffix=".cookies.db")
    os.close(fd)
    try:
        _snapshot_sqlite_db(cookies_db_path, tmp_path)
        con = sqlite3.connect(tmp_path)
        try:
            # `host_key = 'myfitnesspal.com' OR host_key LIKE '%.myfitnesspal.com'`
            # — exact match + any subdomain. Avoids matching unrelated hosts
            # like `notmyfitnesspal.com` that the loose LIKE pattern would.
            rows = con.execute(
                "SELECT name, value, encrypted_value, host_key, last_update_utc FROM cookies "
                "WHERE host_key = ? OR host_key LIKE ?",
                (domain, f"%.{domain}"),
            ).fetchall()
        finally:
            con.close()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    cookies: dict[str, str] = {}
    ranked: dict[str, tuple[int, int]] = {}
    for name, plain, enc, host_key, last_update in rows:
        value = plain if plain else _decrypt_chromium_value_macos(enc, aes_key, host_key)
        if value is None or "�" in value:
            continue
        # MyFitnessPal issues *different* values of the same cookie name to
        # different hosts: `__Secure-next-auth.session-token` exists for both
        # www.myfitnesspal.com and api.myfitnesspal.com. Collapsing rows by name
        # alone let SQLite's row order pick which one we send to the website, so
        # identical code intermittently authenticated or bounced to
        # /account/logout. Rank by how well a row's host matches the host we
        # actually talk to, then by recency.
        rank = (_host_rank(host_key), int(last_update or 0))
        if name in ranked and ranked[name] >= rank:
            continue
        ranked[name] = rank
        cookies[name] = value
    return cookies


def _host_rank(host_key: str) -> int:
    """Score a cookie row's host for requests aimed at www.myfitnesspal.com."""
    host = (host_key or "").lstrip(".")
    if host == "www.myfitnesspal.com":
        return 3
    if host == "myfitnesspal.com":
        return 2
    if host.startswith("api."):
        return 0
    return 1


def _has_real_mfp_session(cookies: dict[str, str]) -> bool:
    """True if the cookie set looks like an authenticated MFP session.

    A pre-auth response can include cookies with 'auth' in the name
    (e.g. `__Host-next-auth.csrf-token`), so we look for the specific
    session-token markers MFP actually uses.
    """
    return any("session-token" in name or name == "_mfp_session" for name in cookies)


def _chromium_cookie_dbs(browser_name: str) -> list[Path]:
    """Every profile's cookie DB for one browser, newest-written first.

    Only the `Default` profile used to be searched, so a MyFitnessPal login
    living in any other Chrome profile was invisible — and, worse, a stale
    `Default` profile would be picked up instead of the profile actually in use.
    """
    relative_paths = _CHROMIUM_COOKIES_PATHS_MACOS.get(browser_name)
    if not relative_paths:
        return []
    appsup = Path.home() / "Library" / "Application Support"
    found: list[Path] = []
    for relative in relative_paths:
        exact = appsup / relative
        if exact.exists():
            found.append(exact)
        # Swap the profile segment ("Default") for a glob so "Profile 1",
        # "Profile 2", ... are covered too.
        parts = relative.split("/")
        if "Default" in parts:
            pattern = "/".join("*" if part == "Default" else part for part in parts)
            found.extend(path for path in appsup.glob(pattern) if path != exact)
    unique = list(dict.fromkeys(found))
    return sorted(unique, key=lambda path: path.stat().st_mtime, reverse=True)


def _try_extract_from_chromium_browser(
    service: str,
) -> dict[str, str] | None:
    """Extract cookies from one specific Chromium browser by Safe Storage
    service name (e.g. 'Arc Safe Storage'). Returns None on any failure."""
    for _, cookies in _chromium_profile_cookie_sets(service):
        return cookies
    return None


def _chromium_profile_cookie_sets(service: str):
    """Yield (profile_label, cookies) for each profile that holds MFP cookies."""
    browser_name = service.replace(" Safe Storage", "").strip()
    db_paths = _chromium_cookie_dbs(browser_name)
    if not db_paths:
        logger.debug(f"No cookies DB found for '{browser_name}'")
        return
    password = _safe_storage_keychain_password(service)
    if not password:
        logger.debug(f"Keychain lookup failed for '{service}'")
        return
    try:
        aes_key = _derive_chromium_aes_key_macos(password)
    except Exception as e:
        logger.debug(f"Key derivation failed for '{browser_name}': {e}")
        return
    for db_path in db_paths:
        try:
            cookies = _extract_chromium_cookies_macos(db_path, aes_key)
        except Exception as e:
            logger.debug(f"Cookie extraction failed for '{db_path}': {e}")
            continue
        if not cookies:
            continue
        profile = db_path.parent.name if db_path.parent.name != "Network" else db_path.parts[-3]
        label = browser_name if profile == "Default" else f"{browser_name} ({profile})"
        yield label, cookies


def try_chromium_browsers_for_session_cookies(
    validate=None,
) -> tuple[str, dict[str, str]] | None:
    """Discover installed Chromium browsers (macOS only) and return one whose
    MyFitnessPal session works.

    Args:
        validate: Optional predicate run against a candidate cookie set. When
            given, scanning continues past any browser/profile it rejects, and
            the first accepted set is returned. Without it — or when nothing is
            accepted — the first set carrying session cookies is returned, which
            keeps legacy-only callers working.

    Every browser is checked before giving up, because the presence of a
    session cookie says nothing about whether MyFitnessPal still honours it:
    the previous "first browser with a session-token name wins" rule made a
    dead Chrome session shadow a live one in another browser, and logged it as
    "Found valid MyFitnessPal session".

    Returns a (browser_name, cookies) tuple, or None if no browser yielded
    a usable session.
    """
    if sys.platform != "darwin":
        return None
    services = _list_chromium_safe_storage_services_macos()
    if not services:
        logger.debug("No Chromium Safe Storage entries found in keychain")
        return None
    fallback: tuple[str, dict[str, str]] | None = None
    for service in services:
        for label, cookies in _chromium_profile_cookie_sets(service):
            if not _has_real_mfp_session(cookies):
                logger.debug(f"{label} had {len(cookies)} cookies but no session token")
                continue
            if validate is None:
                logger.info(f"Found a MyFitnessPal session in {label} ({len(cookies)} cookies)")
                return label, cookies
            if validate(cookies):
                logger.info(f"Found a live MyFitnessPal session in {label}")
                return label, cookies
            logger.debug(f"{label} held a MyFitnessPal session that the site rejected")
            fallback = fallback or (label, cookies)
    if fallback:
        logger.warning(
            f"No browser held a session MyFitnessPal still accepts; falling back to "
            f"{fallback[0]}. Log in again at myfitnesspal.com to restore full access."
        )
    return fallback
