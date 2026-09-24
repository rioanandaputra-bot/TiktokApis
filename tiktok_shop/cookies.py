"""A session's cookies in the Netscape cookie-file format sellers export and store.

Parsed form: {(domain, name): {domain, path, secure, expires, value}}.
"""
import http.cookiejar
from typing import Any, Dict, Tuple


def parse_netscape(cookies_content: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Parse Netscape cookie file content into a dictionary."""
    out = {}
    if not cookies_content:
        return out
    for line in cookies_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split("\t")
        if len(p) >= 7:
            domain, _, cookie_path, secure, expires, name, value = p[:7]
            out[(domain, name)] = {
                "domain": domain,
                "path": cookie_path or "/",
                "secure": secure.upper() == "TRUE",
                "expires": int(expires) if expires.isdigit() else 0,
                "value": value,
            }
    return out


def write_netscape(out: Dict[Tuple[str, str], Dict[str, Any]]) -> str:
    """Format dictionary back into Netscape HTTP Cookie file string."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file! Do not edit.",
    ]
    for (domain, name), m in sorted(out.items()):
        if not m.get("value"):
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(
            f"{domain}\t{flag}\t{m.get('path', '/')}\t{'TRUE' if m.get('secure') else 'FALSE'}\t"
            f"{m.get('expires', 0)}\t{name}\t{m.get('value')}"
        )
    return "\n".join(lines) + "\n"


def load_jar(cookies_content: str) -> http.cookiejar.CookieJar:
    """Load Netscape cookie string into a CookieJar."""
    cj = http.cookiejar.CookieJar()
    parsed = parse_netscape(cookies_content)
    for (domain, name), m in parsed.items():
        if m.get("value"):
            c = http.cookiejar.Cookie(
                version=0, name=name, value=m["value"],
                port=None, port_specified=False,
                domain=domain, domain_specified=bool(domain), domain_initial_dot=domain.startswith("."),
                path=m.get("path") or "/", path_specified=True,
                secure=bool(m.get("secure")), expires=m.get("expires") or None,
                discard=False, comment=None, comment_url=None, rest={}
            )
            cj.set_cookie(c)
    return cj
