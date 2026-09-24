"""A session's cookies in the Netscape cookie-file format sellers export and store.

Parsed form: {(domain, name): {domain, path, secure, expires, value}}.
"""
import base64
import http.cookiejar
import json
from typing import Any, Dict, Iterable, Optional, Tuple

from . import constants as tt

# A session's cookies as (name, value, domain, path) -- what callers hand to this package.
Cookie = Tuple[str, str, str, str]


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


def cookie_value(cookies: Iterable[Cookie], name: str) -> Optional[str]:
    """The cookie's value, preferring the Affiliate Center host's own when names repeat."""
    exact = fallback = None
    for n, value, domain, _ in cookies:
        if n != name or not value:
            continue
        if (domain or "").lstrip(".") == tt.AFFILIATE_DOMAIN:
            exact = value
        elif fallback is None:
            fallback = value
    return exact or fallback


def _jwt_claims(token: Optional[str]) -> Dict[str, Any]:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)).decode())
    except Exception:
        return {}


def seller_id(cookies: Iterable[Cookie]) -> Optional[str]:
    """oec_seller_id from the seller-center cookies, else from its seller JWTs."""
    cookies = list(cookies)
    for name in ("oec_seller_id_unified_seller_env", "global_seller_id_unified_seller_env",
                 "oec_seller_id", "seller_id"):
        value = cookie_value(cookies, name)
        if value:
            return str(value)
    for name in ("UNIFIED_SELLER_TOKEN", "SELLER_TOKEN"):
        claims = _jwt_claims(cookie_value(cookies, name))
        found = claims.get("SellerId") or claims.get("OecShopId") or claims.get("OecSellerId") \
            or claims.get("GlobalSellerId")
        if found:
            return str(found)
        sellers_map = claims.get("GlobalSellerMap")
        if isinstance(sellers_map, dict):
            for key, value in sellers_map.items():
                if key:
                    return str(key)
                for seller in (value or {}).get("Sellers") or [] if isinstance(value, dict) else []:
                    if isinstance(seller, dict) and seller.get("SellerID"):
                        return str(seller["SellerID"])
    return None


def region(cookies: Iterable[Cookie]) -> Optional[str]:
    """The shop's region from its seller JWTs, if they name one."""
    cookies = list(cookies)
    for name in ("UNIFIED_SELLER_TOKEN", "SELLER_TOKEN"):
        claims = _jwt_claims(cookie_value(cookies, name))
        found = claims.get("ShopRegion") or claims.get("region")
        if found and str(found).strip():
            return str(found).upper()
        sellers_map = claims.get("GlobalSellerMap")
        if isinstance(sellers_map, dict):
            for value in sellers_map.values():
                for seller in (value.get("Sellers") or []) if isinstance(value, dict) else []:
                    if isinstance(seller, dict) and seller.get("ShopRegion"):
                        return str(seller["ShopRegion"]).upper()
    return None


def cffi_jar(parsed: Dict[Tuple[str, str], Dict[str, Any]]):
    """A parse_netscape() dict as a curl_cffi cookie jar."""
    from curl_cffi import requests as cffi_requests
    jar = cffi_requests.cookies.Cookies()
    for (domain, name), m in parsed.items():
        if m.get("value"):
            try:
                jar.set(name, m["value"], domain=domain, path=m.get("path", "/"))
            except Exception:
                pass
    return jar
