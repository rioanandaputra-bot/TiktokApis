"""X-Tts-Oec-Bsid for signed Affiliate Center requests, from TikTok's own unisec SDK.

The page's SDK signs each request: `window.lucifer._s({method, body, url, flag})` over the URL
that already carries msToken, X-Bogus and X-Gnarly. Endpoints like invitation_group/detail check
that set as a whole and answer anything else with code 10000; lenient ones accept an unsigned
call but not a signed one without a matching BSID. So every signed call gets its own BSID.

LuciferBSIDSigner (signing/lucifer_bsid.py) boots the official loader/core in a Node shim, keeps
one `bsid.js --serve` alive for every session (~2 ms a BSID) and mints bs tokens through the
SDK's own /bs/rt. The request must then send that token as its `oec_lucifer` cookie.

A token belongs to one cookie session; `Bsid` keeps it in memory and in an optional shared
TokenStore (e.g. Redis, so every process of an app signs with the same token).
"""
import logging
import re
import threading
import time
import urllib.parse
from typing import Dict, Iterable, List, Optional, Protocol, Tuple

from . import constants as tt
from ._upstream import lucifer_bsid
from .cookies import Cookie
from .device import Device

logger = logging.getLogger("tiktok_shop.bsid")

TOKEN_COOKIE = "oec_lucifer"
TOKEN_TTL_S = 12 * 3600
_TOKEN = re.compile(r"^[0-9a-f]{160,}$", re.I)


class TokenStore(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl_s: int) -> None: ...
    def delete(self, key: str) -> None: ...


def cookie_header(cookies: Iterable[Cookie], token: Optional[str]) -> str:
    """The session's cookies as the page sends them, with `token` as oec_lucifer."""
    pairs: Dict[str, str] = {}
    for name, value, domain, _ in cookies:
        if value and tt.COOKIE_DOMAIN.lstrip(".") in (domain or "") and name != TOKEN_COOKIE:
            pairs.setdefault(name, value)
    if token:
        pairs[TOKEN_COOKIE] = token
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def strip_bsid(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = "&".join(p for p in parsed.query.split("&") if p and not p.startswith("X-Tts-Oec-Bsid="))
    return urllib.parse.urlunsplit(parsed._replace(query=query))


class Bsid:
    """BSIDs and bs tokens for many cookie sessions, each named by a `key` (e.g. a shop id)."""

    def __init__(self, store: Optional[TokenStore] = None, profile: str = tt.BSID_PROFILE,
                 store_prefix: str = "tiktok:bs_token:"):
        self.store = store
        self.prefix = store_prefix
        self.signer = lucifer_bsid.LuciferBSIDSigner(profile=profile)
        self._tokens: Dict[str, str] = {}
        self._lock = threading.Lock()

    def token(self, key: str, cookies: List[Cookie], device: Device) -> str:
        """The session's bs token: memory, then the shared store, then a fresh mint."""
        with self._lock:
            if key in self._tokens:
                return self._tokens[key]
        cached = None
        if self.store is not None:
            try:
                cached = self.store.get(self.prefix + key)
            except Exception:
                cached = None
        if cached and _TOKEN.match(cached):
            tok = cached
        else:
            t0 = time.monotonic()
            tok = self.signer.mint_token(cookie=cookie_header(cookies, None), navigator=device.navigator())
            logger.info("[bsid] minted bs token for %s in %.2fs", key, time.monotonic() - t0)
            if self.store is not None:
                try:
                    self.store.set(self.prefix + key, tok, TOKEN_TTL_S)
                except Exception:
                    pass
        with self._lock:
            self._tokens[key] = tok
        return tok

    def forget(self, key: str) -> None:
        """Drop the session's token (TikTok refused a request signed with it, or the cookies were
        re-minted); the next signed call mints a new one."""
        with self._lock:
            self._tokens.pop(key, None)
        if self.store is not None:
            try:
                self.store.delete(self.prefix + key)
            except Exception:
                pass

    def sign(self, key: str, cookies: List[Cookie], device: Device,
             requests: List[Tuple[str, str, str]]) -> Tuple[List[str], str]:
        """(BSIDs for [(method, pre_sign_url, body)], the token they carry). pre_sign_url is the
        full URL as it will be sent, minus X-Tts-Oec-Bsid."""
        tok = self.token(key, cookies, device)
        bsids = self.signer.sign(cookie=cookie_header(cookies, tok), navigator=device.navigator(),
                                 requests=requests)
        return bsids, tok

    def sign_url(self, key: str, cookies: List[Cookie], device: Device, method: str, url: str,
                 body: str) -> Tuple[str, str]:
        """(`url` with a BSID signed for exactly this request, the token to send as oec_lucifer)."""
        pre = strip_bsid(url)
        (bsid,), tok = self.sign(key, cookies, device, [(method.upper(), pre, body)])
        return f"{pre}&X-Tts-Oec-Bsid={bsid}", tok
