"""Signed TikTok Shop web requests for many sessions (shops), with TikTok's recovery paths.

What a request looks like -- the common query, headers, X-Bogus/X-Gnarly over exactly the
query and body sent, a BSID for exactly that URL -- and what to do when TikTok pushes back
(401/403, a bdturing challenge) lives here. Everything that belongs to the application --
which cookies, device and proxy a shop has, how its session is refreshed or re-minted, what
"needs re-auth" means -- comes from a `Host` the application implements.

Recovery, per request (at most MAX_RECOVERY_ROUNDS recovery rounds, never looping):
  401/403                  -> host.refresh_auth, retry once;
  bdturing challenge        -> drop the bs token; on a first try re-mint ttwid and retry; on a
                               retry re-mint the whole device -- queued when a user waits (the
                               job decides on re-auth), inline in background jobs, and if that
                               fails too -> host.needs_reauth. There is no captcha solver: a
                               challenge has so far always meant a malformed request, so each
                               one is logged with its endpoint.
"""
import base64
import gzip
import json
import logging
import urllib.parse
import zlib
from typing import Any, Dict, List, Optional, Protocol, Tuple

from . import constants as tt
from .bsid import Bsid, TOKEN_COOKIE
from .cookies import Cookie, cookie_value
from .device import Device
from .signing import x_bogus, x_gnarly
from .web import browser_urlencode, client_hints

logger = logging.getLogger("tiktok_shop.client")

MAX_RECOVERY_ROUNDS = 2
# Response codes TikTok uses to ask for verification, with the conf in `data`.
_CHALLENGE_CODES = (10000, 10005, 40001, 98001004)


# ============================================================================ request parts

def common_query(cookies: List[Cookie], device: Device, region: str, seller_id: Optional[str],
                 is_seller: bool = False) -> Dict[str, str]:
    """The query every Affiliate Center (or seller center) API call carries, as the page builds it."""
    q = {
        "user_language": cookie_value(cookies, "i18next") or cookie_value(cookies, "lang") or "en",
        "aid": tt.AID_SELLER if is_seller else tt.AID_AFFILIATE,
        "app_name": tt.APP_NAME_SELLER if is_seller else tt.APP_NAME_AFFILIATE,
        "device_id": "0",
        # No borrowed fallback: another device's s_v_web_id would tie this session to it.
        "fp": cookie_value(cookies, "s_v_web_id") or cookie_value(cookies, "fp") or "",
        "device_platform": "web",
        "cookie_enabled": "true",
        "screen_width": str(device.screen[0]),
        "screen_height": str(device.screen[1]),
        "browser_language": device.language,
        "browser_platform": device.platform,
        "browser_name": "Mozilla",
        "browser_version": device.browser_version,
        "browser_online": "true",
        "timezone_name": device.timezone,
        "shop_region": region,
    }
    if seller_id:
        q["oec_seller_id"] = str(seller_id)
    ms_token = cookie_value(cookies, "msToken")
    if ms_token:
        q["msToken"] = ms_token
    return q


def marketplace_headers(device: Device, region: str, referer: Optional[str] = None,
                        app_key: Optional[str] = tt.MCS_APP_KEY) -> Dict[str, str]:
    """Headers of an Affiliate Center API fetch."""
    headers = {
        "content-type": "application/json",
        "accept": "*/*",
        "accept-language": device.accept_language,
        "origin": tt.AFFILIATE,
        "referer": referer or tt.AFFILIATE_MARKETPLACE_PAGE,
        "user-agent": device.user_agent,
        **client_hints(device),
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "priority": "u=1, i",
        "x-tt-oec-region": region,
        "x-setting-flag": "1",
    }
    if app_key:
        headers["x-mcs-appkey"] = app_key
    return headers


def base_headers(device: Device) -> Dict[str, str]:
    """What every request sends unless the caller overrides it."""
    return {"accept": "application/json, text/plain, */*", "accept-language": device.accept_language,
            "user-agent": device.user_agent, "origin": tt.AFFILIATE, "referer": f"{tt.AFFILIATE}/"}


def body_bytes(body: Any) -> Tuple[Optional[bytes], str]:
    """(wire bytes, signed text) for a request body. dict/list go out as compact JSON -- what the
    browser's JSON.stringify sends."""
    if body is None:
        return None, ""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body), bytes(body).decode("utf-8", errors="replace")
    text = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
    return text.encode("utf-8"), text


def body_text(data: Any) -> str:
    return body_bytes(data)[1]


def signed_url(base_url: str, query: Any, signed_body: str, device: Device) -> str:
    """`base_url` + `query` (a dict or ordered pairs, kept in order) + X-Bogus/X-Gnarly over
    exactly that query and `signed_body`."""
    q = browser_urlencode(query)
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{q}&X-Bogus={x_bogus(q, signed_body, device)}&X-Gnarly={x_gnarly(q, signed_body, device)}"


def resign_url(url: str, signed_body: str, device: Device) -> str:
    """`url` with fresh X-Bogus/X-Gnarly (and no BSID: sending attaches a new one)."""
    parsed = urllib.parse.urlparse(url)
    if not parsed.query:
        return url
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if k not in ("X-Bogus", "X-Gnarly", "X-Tts-Oec-Bsid")]
    base_qs = urllib.parse.urlencode(pairs)
    new_qs = f"{base_qs}&X-Bogus={x_bogus(base_qs, signed_body, device)}&X-Gnarly={x_gnarly(base_qs, signed_body, device)}"
    return urllib.parse.urlunparse(parsed._replace(query=new_qs))


def decode_body(raw: bytes) -> Any:
    """JSON from a response body that may be gzip/deflate compressed or base64 encoded;
    {"raw_text": ...} when it is none of those (e.g. an HTML gateway page)."""
    if not raw:
        return None
    if len(raw) > 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif len(raw) > 2 and raw[0] == 0x78:
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
    try:
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        try:
            return json.loads(base64.b64decode(raw).decode("utf-8", errors="ignore"))
        except Exception:
            return {"raw_text": raw.decode("utf-8", errors="ignore")}


def ok_data(raw: bytes, what: str) -> Dict[str, Any]:
    """The decoded response, or RuntimeError naming `what` when TikTok did not answer code 0."""
    res = decode_body(raw)
    if isinstance(res, dict) and res.get("code") == 0:
        return res
    if isinstance(res, dict) and "raw_text" in res:
        raise RuntimeError(f"{what} failed: code=HTML_GATEWAY TikTok gateway returned HTML instead of JSON")
    code = res.get("code") if isinstance(res, dict) else "?"
    msg = res.get("message") if isinstance(res, dict) else "unreadable body"
    raise RuntimeError(f"{what} failed: code={code} {msg}")


def challenge_conf(headers: Any, content: bytes) -> Optional[str]:
    """The bdturing conf (JSON text) TikTok asks the page to verify with, if it asked: the
    `bdturing-verify` / `x-vc-bdturing-parameters` header, or a verification code in the body."""
    conf = headers.get("bdturing-verify") or headers.get("x-vc-bdturing-parameters")
    if conf or not content:
        return conf
    try:
        body = json.loads(content.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("code") not in _CHALLENGE_CODES:
        return None
    data = body.get("data")
    if isinstance(data, dict):
        cand = data.get("bdturing-verify") or data.get("bdturing_verify") or data.get("verify_center")
        if cand:
            return json.dumps(cand) if isinstance(cand, dict) else str(cand)
    elif isinstance(body.get("verify_center"), dict):
        return json.dumps(body["verify_center"])
    return None


# ============================================================================ sessions

class Host(Protocol):
    """What the application supplies for a session `key` (a shop)."""

    def device(self, key: str) -> Device: ...
    def cookies(self, key: str) -> List[Cookie]: ...
    def http_session(self, key: str) -> Any: ...                     # a curl_cffi Session
    def set_cookie(self, key: str, name: str, value: str, domain: str) -> None: ...
    def reset(self, key: str) -> None: ...                           # drop cached jar/session
    def refresh_auth(self, key: str) -> None: ...                    # after 401/403
    def remint_ttwid(self, key: str) -> bool: ...
    def remint_device(self, key: str) -> bool: ...                   # inline, slow (~14 s)
    def queue_device_remint(self, key: str) -> None: ...
    def needs_reauth(self, key: str) -> None: ...
    def inline_device_remint(self) -> bool: ...                      # background job?


class Client:
    """Sends requests for many sessions through one Host and one BSID signer."""

    def __init__(self, host: Host, bsid: Bsid):
        self.host = host
        self.bsid = bsid

    def signed_call(self, method: str, base_url: str, key: str, query: Any, body: Any = None,
                    headers: Optional[Dict[str, str]] = None, sign_body: bool = True) -> bytes:
        """One signed call: X-Bogus/X-Gnarly over exactly this query and body with the session's
        device; request() adds the BSID. sign_body=False sends the body but signs an empty one,
        as the page's SDKs treat a multipart FormData upload."""
        data, signed = body_bytes(body)
        if not sign_body:
            signed = ""
        url = signed_url(base_url, query, signed, self.host.device(key))
        return self.request(method, url, key, data=data, headers=headers, is_json=False, signed_body=signed)

    def request(self, method: str, url: str, key: Optional[str], data: Any = None,
                headers: Optional[Dict[str, str]] = None, is_json: bool = True,
                signed_body: Optional[str] = None, _is_retry: bool = False,
                _rounds: int = 0) -> bytes:
        """Send one request for session `key` with its cookies; recover as the module says."""
        if "X-Bogus=" in url:
            if not key:
                # A signed call without its session once went out with an empty BSID and TikTok
                # rejected 800 invitations (98001002).
                raise RuntimeError("No shop context bound for a signed TikTok request")
            if is_json and data is not None:
                # Sent exactly as signed: compact JSON.
                data, is_json = json.dumps(data, separators=(",", ":")), False
                headers = {"content-type": "application/json", **(headers or {})}
            url, token = self.bsid.sign_url(key, self.host.cookies(key), self.host.device(key), method, url,
                                            body_text(data) if signed_body is None else signed_body)
            self.host.set_cookie(key, TOKEN_COOKIE, token, tt.AFFILIATE_DOMAIN)

        device = self.host.device(key)
        h = {**base_headers(device), **(headers or {})}
        h.pop("cookie", None)
        from curl_cffi import requests as cffi_requests
        jar = cffi_requests.cookies.Cookies()
        for name, value, domain, path in self.host.cookies(key):
            if value and (domain or "").endswith(tt.COOKIE_DOMAIN.lstrip(".")):
                try:
                    jar.set(name, value, domain=domain, path=path or "/")
                except Exception:
                    pass
        kwargs = {"method": method, "url": url, "headers": h, "cookies": jar, "timeout": 30}
        if data is not None:
            kwargs["json" if is_json else "data"] = data
        resp = self.host.http_session(key).request(**kwargs)
        if _is_retry:
            logger.info("[client] retry status=%d", resp.status_code)

        def retry(new_url: str, attempts: int) -> bytes:
            return self.request(method, new_url, key, data=data, headers=headers, is_json=is_json,
                                signed_body=signed_body, _is_retry=True, _rounds=attempts)

        def resigned() -> str:
            return resign_url(url, body_text(data) if signed_body is None else signed_body, self.host.device(key))

        if resp.status_code in (401, 403) and key and not _is_retry:
            logger.warning("[client] HTTP %d for %s on %s, refreshing the session", resp.status_code, key, url.split("?")[0])
            try:
                self.host.refresh_auth(key)
                self.host.reset(key)
                return retry(url, _rounds)
            except Exception as e:
                logger.warning("[client] refresh retry for %s failed: %s", key, e)

        conf = challenge_conf(resp.headers, resp.content) if key else None
        if not conf:
            return resp.content
        logger.warning("[client] bdturing challenge for %s on %s %s (attempt %d)", key, method,
                       urllib.parse.urlsplit(url).path, _rounds)
        self.bsid.forget(key)                      # a refusal may mean the bs token went stale
        if _rounds >= MAX_RECOVERY_ROUNDS:
            logger.warning("[client] challenge persisted after %d re-mints for %s; needs re-auth", _rounds, key)
            self.host.needs_reauth(key)
            return resp.content

        if not _is_retry:
            try:
                if self.host.remint_ttwid(key):
                    self.host.reset(key)
                    return retry(resigned(), _rounds + 1)
            except Exception as e:
                logger.warning("[client] ttwid re-mint for %s failed: %s", key, e)
            return resp.content                    # the caller sees TikTok's answer; next call retries
        elif not self.host.inline_device_remint():
            # A user is waiting: queue the slow device re-mint and answer now; the job marks the
            # session for re-auth only if the re-mint fails.
            try:
                self.host.queue_device_remint(key)
            except Exception as e:
                logger.warning("[client] could not queue a device re-mint for %s: %s", key, e)
                self.host.needs_reauth(key)
            return resp.content
        else:
            try:
                if self.host.remint_device(key):
                    self.host.reset(key)
                    return retry(resigned(), _rounds + 1)
            except Exception as e:
                logger.warning("[client] device re-mint for %s failed: %s", key, e)
        self.host.needs_reauth(key)
        return resp.content
