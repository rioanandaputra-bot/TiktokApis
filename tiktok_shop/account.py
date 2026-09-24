"""A seller session's upkeep: verifying it is alive and whose it is, refreshing it through the
SSO redirect chain, and minting the device cookies the Affiliate Center's sensitive endpoints
need (ttwid, s_v_web_id). Cookies are the Netscape parse (tiktok_shop.cookies.parse_netscape):
{(domain, name): {domain, path, secure, expires, value}}.
"""
import base64
import copy
import email.utils
import json
import logging
import time
import urllib.parse
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional, Tuple

from curl_cffi import requests as cffi_requests

from . import constants as tt
from .cookies import cffi_jar
from .device import Device, legacy
from .web import client_hints

logger = logging.getLogger("tiktok_shop.account")

Cookies = Dict[Tuple[str, str], Dict[str, Any]]


def _device_headers(dev: Device) -> Dict[str, str]:
    """User-agent, accept-language and client hints of the session's device."""
    return {"user-agent": dev.user_agent, "accept-language": dev.accept_language, **client_hints(dev)}


def verify(out: Cookies, dev: Optional[Device] = None) -> Dict[str, Any]:
    """Verify shop account credentials and extract Seller ID & metadata.

    Uses multi-signal verification:
    1. Passport account info endpoint (/passport/account/info/v2/)
    2. Affiliate Seller Shop Info endpoint (/api/v1/oec/affiliate/seller/shop/info)
    3. Affiliate IM Token endpoint (/api/v1/oec/affiliate/seller/im/get/token)

    Only raises SessionDead if ALL signals fail to authenticate. `dev` is the session's device;
    the legacy one when unknown.
    """
    dev = dev or legacy()
    jar = cffi_jar(out)
    info = {}
    passport_ok = False

    passport_headers = {
        "accept": "application/json, text/plain, */*",
        **_device_headers(dev),
        "referer": f"{tt.SELLER}/homepage",
        "origin": tt.SELLER,
    }

    try:
        resp = cffi_requests.get(f"{tt.SELLER}/passport/account/info/v2/?get_info_type=2&aid={tt.AID_SELLER}", cookies=jar, headers=passport_headers, impersonate=dev.impersonate, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("message") == "success":
                info = data.get("data", {})
                passport_ok = True
    except Exception as e:
        logger.warning(f"[verify_account_info] Passport verification notice: {e}")

    result = {
        "seller_name": info.get("name") or info.get("screen_name"),
        "name": info.get("name") or info.get("screen_name"),
        "email": info.get("email"),
        "user_id": info.get("user_id"),
        "store_country": info.get("store_country") or "id",
    }

    # Extract Seller ID & IM Shop ID from cookies or JWT tokens
    for (d, n), m in out.items():
        if n in ("oec_seller_id_unified_seller_env", "oec_seller_id", "seller_id") and m.get("value"):
            result["seller_id"] = m["value"]
        elif n in ("SHOP_ID", "im_shop_id", "IM_SHOP_ID") and m.get("value"):
            result["im_shop_id"] = m["value"]
        elif n in ("SELLER_TOKEN", "UNIFIED_SELLER_TOKEN") and m.get("value"):
            try:
                parts = m["value"].split(".")
                if len(parts) >= 2:
                    p = parts[1] + "=" * (-len(parts[1]) % 4)
                    jwt_data = json.loads(base64.b64decode(p).decode())
                    sid = jwt_data.get("SellerId") or jwt_data.get("OecShopId") or jwt_data.get("OecSellerId")
                    if sid and "seller_id" not in result:
                        result["seller_id"] = str(sid)
                    im_id = jwt_data.get("ImShopId") or jwt_data.get("ShopId")
                    if im_id and "im_shop_id" not in result:
                        result["im_shop_id"] = str(im_id)
            except Exception:
                pass

    seller_id = result.get("seller_id")
    region = (result.get("store_country") or "ID").upper()

    affiliate_verified = False

    # Fetch real E-Commerce Shop Name from Affiliate Seller Shop Info API if seller_id exists
    if seller_id:
        try:
            shop_info_url = f"{tt.AFFILIATE}/api/v1/oec/affiliate/seller/shop/info?oec_seller_id={seller_id}&shop_region={region}"
            headers = {
                **_device_headers(dev),
                "origin": tt.AFFILIATE,
                "referer": f"{tt.AFFILIATE}/connection/creator?shop_region={region}&shop_id={seller_id}",
            }
            s_resp = cffi_requests.get(shop_info_url, cookies=jar, headers=headers, impersonate=dev.impersonate, timeout=15)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                if s_data.get("code") == 0 and s_data.get("shop_info"):
                    s_info = s_data["shop_info"]
                    affiliate_verified = True
                    if s_info.get("shop_name"):
                        result["seller_name"] = s_info["shop_name"]
                    avatar_list = s_info.get("shop_avatar", {}).get("thumb_url_list") or s_info.get("shop_avatar", {}).get("url_list")
                    if avatar_list and len(avatar_list) > 0:
                        result["shop_avatar"] = avatar_list[0]
        except Exception as e:
            logger.warning(f"[verify_account_info] Warning fetching shop_info: {e}")

    # Fallback: If im_shop_id is missing, try fetching IM Token API using cookies
    if not result.get("im_shop_id"):
        try:
            seller_id_param = result.get("seller_id", "")
            im_url = f"{tt.AFFILIATE}/api/v1/oec/affiliate/seller/im/get/token?shop_region={region}&oec_region={region}&oec_seller_id={seller_id_param}"
            headers = {
                **_device_headers(dev),
                "referer": f"{tt.AFFILIATE}/seller/im?enter_from=nav_im_entry&shop_region={region}&shop_id={seller_id_param}",
            }
            im_resp = cffi_requests.get(im_url, cookies=jar, headers=headers, impersonate=dev.impersonate, timeout=15)
            if im_resp.status_code == 200:
                im_data = im_resp.json().get("data", {})
                im_user = im_data.get("user", {})
                user_id_val = im_user.get("user_id") or im_data.get("shop_id")
                if user_id_val:
                    result["im_shop_id"] = str(user_id_val)
                    affiliate_verified = True
        except Exception as im_err:
            logger.warning(f"[verify_account_info] Warning fetching IM token for im_shop_id: {im_err}")

    if not result.get("shop_avatar") and info.get("avatar_url"):
        result["shop_avatar"] = info["avatar_url"]

    # If NEITHER passport nor affiliate APIs validated the session, raise SessionDead
    if not passport_ok and not affiliate_verified:
        raise SessionDead("Account verification rejected session: credentials expired or revoked by TikTok/Tokopedia.")

    # STRICT REQUIREMENT VALIDATION: Both seller_id AND im_shop_id MUST be present!
    if not result.get("seller_id"):
        raise ValueError(
            "Account verification failed: 'seller_id' was not found in session. "
            "The sub-account might not have the required shop permissions or was removed from TikTok Seller Center."
        )

    if not result.get("im_shop_id"):
        raise ValueError(
            "Account verification failed: 'im_shop_id' was not found in session or IM account. "
            "Please ensure the sub-account is granted Affiliate Manager permissions."
        )

    return result


class SessionDead(Exception):
    """TikTok confirmed the session is dead (an AUTHENTICATED call was rejected) — NOT a
    transient network failure, and NOT the SSO login-page redirect (that one is normal).
    Dead sessions must stop being retried: the cron skips inactive shops and the only recovery
    is a fresh cookie upload."""


def sso_refresh(out: Cookies, dev: Optional[Device] = None) -> Cookies:
    """Attempt SSO refresh against the SSO login page and Affiliate portal, capturing Set-Cookie headers across redirect chain."""
    updated = copy.deepcopy(out)
    jar = cffi_jar(updated)

    urls_to_visit = [
        (tt.SSO_LOGIN_PAGE, {"referer": tt.SELLER}),
        (f"{tt.AFFILIATE}/connection/creator?shop_region={tt.REGION}", {"referer": f"{tt.SELLER}/homepage"}),
        (f"{tt.AFFILIATE}/seller/im?shop_region={tt.REGION}&enter_from=nav_im_entry", {"referer": f"{tt.AFFILIATE}/connection/creator"}),
    ]

    for target_url, extra_headers in urls_to_visit:
        try:
            req_headers = _device_headers(dev or legacy())
            if extra_headers:
                req_headers.update(extra_headers)

            resp = cffi_requests.get(target_url, cookies=jar, headers=req_headers, impersonate=(dev or legacy()).impersonate, allow_redirects=True, timeout=20)
            responses_to_process = list(getattr(resp, "history", [])) + [resp]
            for r in responses_to_process:
                headers_list = []
                if hasattr(r.headers, "get_list"):
                    headers_list = r.headers.get_list("set-cookie")
                elif "set-cookie" in r.headers:
                    headers_list = [r.headers["set-cookie"]]

                default_host = urllib.parse.urlparse(getattr(r, "url", target_url)).hostname or tt.COOKIE_DOMAIN.lstrip(".")

                for h in headers_list:
                    c = SimpleCookie()
                    try:
                        c.load(h)
                    except Exception:
                        continue
                    for name, m in c.items():
                        if m.value:
                            cookie_domain = m.get("domain")
                            if cookie_domain:
                                domain = cookie_domain
                            else:
                                domain = default_host

                            exp = m.get("max-age")
                            expires = 0
                            if exp and str(exp).isdigit():
                                expires = int(time.time()) + int(exp)
                            elif m.get("expires"):
                                try:
                                    dt = email.utils.parsedate_to_datetime(m.get("expires"))
                                    if dt:
                                        expires = int(dt.timestamp())
                                except Exception:
                                    expires = 0

                            # Never let an SSO endpoint overwrite a secsdk-minted device id with a
                            # server (non-secsdk) value: that silently breaks sensitive affiliate
                            # endpoints (product/contact) on the next auto-refresh. Keep the good one.
                            if name == "s_v_web_id" and updated.get((domain, name), {}).get("value"):
                                continue

                            old_exp = updated.get((domain, name), {}).get("expires", 0)
                            final_expires = expires if expires > 0 else old_exp

                            updated[(domain, name)] = {
                                "domain": domain,
                                "path": m.get("path") or "/",
                                "secure": bool(m.get("secure")),
                                "expires": final_expires,
                                "value": m.value,
                            }
                            # Also update the in-flight jar for consecutive requests
                            try:
                                jar.set(name, m.value, domain=domain, path=m.get("path") or "/")
                            except Exception:
                                pass
        except Exception as e:
            logger.warning(f"[apply_sso_refresh] Notice requesting '{target_url}': {e}")

    return updated



def ensure_ttwid(out: Cookies, dev: Optional[Device] = None) -> Cookies:
    """Guarantee a ttwid cookie in the jar. Affiliate 'sensitive' endpoints (product_selection/list,
    cmp/contact) reply {code:10000}+bdturing without ttwid. Email-bind mints it in finalize, but an
    SSO refresh or a browser upload that lost it would break affiliate features — so mint on demand.
    Idempotent: no-op when ttwid already present."""
    if any(n == "ttwid" and m.get("value") for (d, n), m in out.items()):
        return out
    try:
        from curl_cffi import requests as _cffi
        from .login import mint_ttwid, use_device
        dev = dev or legacy()
        sess = use_device(_cffi.Session(impersonate=dev.impersonate), dev)
        for (d, n), m in out.items():
            if m.get("value"):
                try:
                    sess.cookies.set(n, m["value"], domain=d, path=m.get("path") or "/")
                except Exception:
                    pass
        if mint_ttwid(sess):
            for c in sess.cookies.jar:
                if c.name == "ttwid" and c.value:
                    dom = c.domain if c.domain.startswith(".") else tt.COOKIE_DOMAIN
                    out[(dom, "ttwid")] = {"domain": dom, "path": "/", "secure": True,
                                           "expires": int(time.time()) + 90 * 86400, "value": c.value}
                    break
    except Exception as e:
        logger.warning("[ensure_ttwid] mint failed: %s", e)
    return out


def ensure_device_cookies(out: Cookies, force: bool = False, dev: Optional[Device] = None) -> Cookies:
    """Ensure the device cookies affiliate features need. `ttwid` unlocks product + contact_types
    (cheap curl mint). `s_v_web_id` (registered only by the real mssdk/secsdk in a browser)
    unlocks the 'sensitive' endpoints like cmp/contact (creator phone/email). The bs token
    (oec_lucifer) is not minted here: tiktok_shop.bsid gets it from TikTok's SDK.
    Idempotent: skips when s_v_web_id + ttwid are already present AND unexpired.

    force=True (bind + reactive re-mint) always re-mints: a bind session's own s_v_web_id is never
    secsdk-valid, so we drop it first and mint a fresh one. Each shop thus gets a UNIQUE device fp —
    deliberately NOT cached/shared across shops (a shared fp adds fraud risk + a shared blast radius
    if one gets flagged). Re-auth reuses the shop's OWN prior fp via the orchestrator carry-over.
    ttwid is (re)minted cheaply via curl — it rotates faster."""
    now = int(time.time())
    # value-present AND not past its explicit expiry (expires<=0 means session/no-expiry -> keep)
    have = {n for (d, n), m in out.items()
            if m.get("value") and (not m.get("expires") or int(m["expires"]) > now)}
    if not force and "s_v_web_id" in have and "ttwid" in have:
        return out
    if force:
        # oec_lucifer goes too: a stored one is a leftover bs token; tiktok_bsid mints its own.
        out = {k: v for k, v in out.items() if k[1] not in ("s_v_web_id", "oec_lucifer", "ttwid")}
    try:
        from .device_mint import mint_device_cookies
        items = [{"name": n, "value": m["value"], "domain": d, "path": m.get("path", "/")}
                 for (d, n), m in out.items() if m.get("value")]
        minted = mint_device_cookies(items, device=(dev or legacy()).navigator()) or {}
        if not minted.get("s_v_web_id"):
            logger.warning("[device_cookies] jsdom mint produced NO s_v_web_id — secsdk failed in "
                           "this env; sensitive endpoints (product/contact) will break")
        for name, ck in minted.items():
            out[(ck["domain"], name)] = {"domain": ck["domain"], "path": ck.get("path", "/"),
                                         "secure": True, "expires": now + 90 * 86400, "value": ck["value"]}
    except Exception as e:
        logger.warning("[device_cookies] browser mint error: %s", e)
    # ttwid always fresh (cheap curl) if the mint didn't include one
    if not any(n == "ttwid" and m.get("value") for (d, n), m in out.items()):
        out = ensure_ttwid(out, dev)
    return out


