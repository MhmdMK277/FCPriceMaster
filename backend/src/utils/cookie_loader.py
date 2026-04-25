"""
Netscape cookie file parser for Playwright.

Cookie files are exported from the browser in Netscape format:
    # Netscape HTTP Cookie File
    .domain.com\tTRUE\t/\tFALSE\t1234567890\tname\tvalue

The output is a list of Playwright cookie dicts ready for
context.add_cookies().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_netscape_cookies(path: str | Path) -> list[dict[str, Any]]:
    """
    Parse a Netscape-format cookie file and return Playwright cookie dicts.
    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if no Twitter session cookies (auth_token / ct0) are found.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Cookie file not found: {p}\n"
            "Export cookies from x.com in Netscape format and save to data/.cookies/x_cookies.txt"
        )

    cookies: list[dict[str, Any]] = []

    for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain, flag, path_field, secure, expires_str, name, value = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
            "\t".join(parts[6:]),  # value may contain tabs
        )

        try:
            expires = int(float(expires_str)) if expires_str else -1
        except ValueError:
            expires = -1

        # Playwright expects domain without a leading dot for exact matches,
        # but keeps the dot for host-only cookies. Pass as-is — browser will
        # match both forms.
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path_field,
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
            "sameSite": "None",
        }
        if expires > 0:
            cookie["expires"] = expires

        cookies.append(cookie)

    # Validate Twitter session cookies
    names = {c["name"] for c in cookies}
    missing = [n for n in ("auth_token", "ct0") if n not in names]
    if missing:
        raise ValueError(
            f"Twitter session cookies are missing or expired: {missing}. "
            "Re-export cookies from x.com in Netscape format and save to "
            "data/.cookies/x_cookies.txt, then restart the Twitter worker."
        )

    return cookies
