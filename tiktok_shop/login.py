"""
TikTok seller sub-account login/activation as importable, I/O-decoupled functions.

Extracted from the proven PoC docs/tiktok/unofficial/login/login_test.py (verified live).
No print()/input() here — callers (arq worker, routes) own the I/O and the SSE captcha relay:
captcha image bytes are RETURNED, click coords are PASSED IN. Every function operates on a
curl_cffi Session passed by the caller, so the same jar can be serialized into Redis between
webhook round-trips (activation email → login → OTP email may span minutes).

Codec for account/password/otp/extra = XOR 0x05 then hex (verified byte-for-byte vs HAR).

Flow (see PLAN-email-binding.md §1):
  bootstrap() -> set_password() [activation]      # host business-sso / seller-id
             -> login() -> ('captcha'|'otp'|'ok')
             -> fetch_captcha()/submit_captcha()  # host verify-sg.byteoversea.com
             -> send_otp()/submit_otp()
             -> finalize() -> export_netscape()    # feed refresh_cookies_pipeline
"""
import json
import logging
import random
import string
import time
import urllib.parse
from typing import Optional, Tuple, Dict, Any

from curl_cffi import requests as cffi

from . import constants as tt
from .cookies import write_netscape
from .device import legacy
from .signing import x_bogus, x_gnarly
from .web import browser_urlencode as _browser_urlencode, client_hints as _client_hints

logger = logging.getLogger("tiktok_shop.login")

# Hosts, app ids, SDK versions and captcha parameters: tiktok_shop_constants (`tt`).


class ConnectError(Exception):
    """Terminal failure in the connect flow (bad creds, expired ticket, TikTok rejection)."""


# TikTok error_code -> clean, user-facing message (Indonesian, matches the portal wording).
_TT_ERR_MESSAGES = {
    1206: "Terlalu banyak permintaan kode verifikasi. Tunggu beberapa menit lalu coba lagi.",
}


def tt_error(resp: dict, context: str) -> str:
    """Human message from a TikTok error body — never dump raw JSON to the user. Prefer a known
    error_code map, then TikTok's own `description`, else a compact context string."""
    ec = resp.get("error_code")
    if ec in _TT_ERR_MESSAGES:
        return _TT_ERR_MESSAGES[ec]
    desc = resp.get("description") or resp.get("message")
    if desc:
        return str(desc)[:200]
    return f"{context} gagal (kode {ec})." if ec is not None else f"{context} gagal."


# --------------------------------------------------------------------------- codec / helpers
def xor_hex(s: str) -> str:
    return "".join(f"{b ^ 0x05:02x}" for b in s.encode())


def gen_verify_fp() -> str:
    def blk(n):
        return "".join(random.choices(string.ascii_letters + string.digits, k=n))
    return f"verify_{blk(8).lower()}_{blk(8)}_{blk(4)}_{blk(4)}_{blk(4)}_{blk(12)}"


def gen_password(n: int = 16) -> str:
    """System-owned strong password for a generated account. Mixed classes, TikTok-safe symbols."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%*+-"]
    pw = [random.choice(p) for p in pools]
    pw += random.choices("".join(pools), k=max(0, n - len(pw)))
    random.shuffle(pw)
    return "".join(pw)


def ck(sess, name: str) -> Optional[str]:
    """Cookie value tolerant of same-name cookies on multiple domains (prefer tokopedia)."""
    try:
        return sess.cookies.get(name)
    except Exception:
        for dom in (tt.COOKIE_DOMAIN, tt.SELLER_DOMAIN, tt.SSO_DOMAIN):
            try:
                v = sess.cookies.get(name, domain=dom)
                if v:
                    return v
            except Exception:
                continue
        return None


# The login session presents one device throughout -- the shop's once it is bound.
# new_session() attaches it; every header and signature reads it.
def use_device(sess, device):
    sess.tt_device = device
    return sess


def _dev(sess):
    return getattr(sess, "tt_device", None) or legacy()


def _ua(sess, **extra) -> dict:
    return {"user-agent": _dev(sess).user_agent, **extra}


def _base_headers(sess, csrf: str, referer: Optional[str] = None) -> dict:
    dev = _dev(sess)
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": dev.accept_language,
        "content-type": "application/x-www-form-urlencoded",
        "origin": tt.SELLER, "referer": referer or f"{tt.SELLER}/", "user-agent": dev.user_agent,
        **_client_hints(dev),
        "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
        "x-requested-with": "XMLHttpRequest", "x-tt-passport-csrf-token": csrf or "",
    }


def _signed(sess, base: str, extra: dict, body: str, verify_fp: str, ms_token: str) -> str:
    q = dict(extra)
    q["fp"] = verify_fp
    q["verifyFp"] = verify_fp
    if ms_token:
        q["msToken"] = ms_token
    qs = _browser_urlencode(q)
    dev = _dev(sess)
    return f"{base}?{qs}&X-Bogus={x_bogus(qs, body, dev)}&X-Gnarly={x_gnarly(qs, body, dev)}"


def _post_form(sess, base: str, form: dict, verify_fp: str, ms_token: str, csrf: str,
               extra_q: Optional[dict] = None, referer: Optional[str] = None) -> dict:
    body = urllib.parse.urlencode(form)
    q = {"aid": tt.AID_SELLER, "language": tt.LANGUAGE}
    if extra_q:
        q.update(extra_q)
    url = _signed(sess, base, q, body, verify_fp, ms_token)
    r = sess.post(url, data=body, headers=_base_headers(sess, csrf, referer))
    try:
        return r.json()
    except Exception:
        raise ConnectError(f"non-JSON from {base}: {r.status_code} {r.text[:200]}")


# --------------------------------------------------------------------------- session
def new_session(proxy: Optional[str] = None, device=None):
    """Fresh HTTP/2 session presenting `device` (default: the legacy identity), with the
    TLS fingerprint of the Chrome its UA claims (same as the app's _get_cffi_session)."""
    device = device or legacy()
    kwargs = {"impersonate": device.impersonate}
    if proxy:
        kwargs["proxies"] = {"all": proxy}
    return use_device(cffi.Session(**kwargs), device)


def dump_cookies(sess) -> list:
    """Serialize the full jar (all domains — captcha continuity needs byteoversea too) for Redis."""
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
            for c in sess.cookies.jar if c.value]


def load_cookies(sess, items: list):
    """Rehydrate a session's jar from dump_cookies() output."""
    for it in items or []:
        try:
            sess.cookies.set(it["name"], it["value"], domain=it.get("domain"), path=it.get("path", "/"))
        except Exception:
            continue
    return sess


def bootstrap(sess) -> Tuple[str, str, str]:
    """Warm cookies on the login page + check_login. Returns (verify_fp, ms_token, csrf)."""
    sess.get(f"{tt.SELLER}/account/login", headers=_ua(sess))
    verify_fp = ck(sess, "s_v_web_id") or gen_verify_fp()
    ms_token = ck(sess, "msToken") or ""
    csrf = ck(sess, "passport_csrf_token") or ""
    url = _signed(sess, f"{tt.SSO}/check_login/",
                  {"service": tt.SSO_SERVICE, "aid": tt.AID_SELLER, "account_sdk_source": "sso", "language": tt.LANGUAGE},
                  "", verify_fp, ms_token)
    sess.get(url, headers=_base_headers(sess, csrf))
    return (ck(sess, "s_v_web_id") or verify_fp,
            ck(sess, "msToken") or ms_token,
            ck(sess, "passport_csrf_token") or csrf)


def mint_ttwid(sess) -> bool:
    """Mint the ttwid anti-bot web-id: POST ttwid/union/register/ (signed) then follow the
    server's redirect_url (callback) which Set-Cookies ttwid on .tokopedia.com.

    ttwid is the single cookie separating a headless bind session from a real browser one:
    without it, affiliate 'sensitive' endpoints (product_selection/list, api_sens/contact_info)
    reply {code:10000} + a bdturing-verify header. Verified end-to-end (see PLAN §1c)."""
    body = json.dumps({"aid": int(tt.AID_SELLER), "service": tt.SELLER_DOMAIN,
                       "unionHost": tt.TTWID_HOST,
                       "needFid": False, "union": True, "fid": ""})
    qs = "msToken="
    dev = _dev(sess)
    reg = (f"{tt.TTWID_HOST}/ttwid/union/register/?{qs}"
           f"&X-Bogus={x_bogus(qs, body, dev)}&X-Gnarly={x_gnarly(qs, body, dev)}")
    try:
        r = sess.post(reg, data=body, headers=_ua(sess, **{"content-type": "application/json",
                                                        "origin": tt.SELLER, "referer": f"{tt.SELLER}/"}))
        redirect = r.json().get("redirect_url")
        if redirect:
            sess.get(redirect, headers=_ua(sess, referer=f"{tt.SELLER}/"))
    except Exception:
        pass
    return any(c.name == "ttwid" and c.value for c in sess.cookies.jar)


# --------------------------------------------------------------------------- activation (set password)
def set_password(sess, invite_ticket: str, subject_aid: str, password: str,
                 user_name: str, verify_fp: str, ms_token: str, csrf: str,
                 default_language: str = tt.LOCALE, email: str = "") -> bool:
    """Accept a member invitation and set the account password (activation email flow).

    invitation/check|accept live on the seller center (tt.SELLER), signed X-Bogus/X-Gnarly,
    referer = the activate-page. Verified via live test. See PLAN-email-binding.md §1a.
    """
    extra_q = {"subject_aid": str(subject_aid), "account_sdk_source": "web",
               "sdk_version": tt.PASSPORT_SDK_ACTIVATE}
    referer = (f"{tt.SELLER}/profile/activate-page?aid={tt.AID_SELLER}&default_language={default_language}"
               f"&email={urllib.parse.quote(email)}&subject_aid={subject_aid}")
    chk = _post_form(sess, f"{tt.SELLER}/passport/aff/web/invitation/check/",
                     {"invite_ticket": invite_ticket}, verify_fp, ms_token, csrf, extra_q, referer)
    if chk.get("message") != "success":
        raise ConnectError(f"invitation/check failed: {json.dumps(chk)[:200]}")

    extra_json = json.dumps({"default_language": default_language, "user_name": user_name},
                            separators=(",", ":"))
    acc = _post_form(sess, f"{tt.SELLER}/passport/aff/web/invitation/accept/", {
        "mix_mode": "1", "invite_ticket": invite_ticket,
        "password": xor_hex(password), "extra": xor_hex(extra_json), "fixed_mix_mode": "1",
    }, verify_fp, ms_token, csrf, extra_q, referer)
    if acc.get("message") != "success":
        raise ConnectError(f"invitation/accept failed: {json.dumps(acc)[:200]}")
    return True


# --------------------------------------------------------------------------- login
def login(sess, email: str, password: str, verify_fp: str, ms_token: str, csrf: str) -> Tuple[str, Any]:
    """One account_login/v3 attempt. Returns:
       ('captcha', decision_conf_dict) | ('otp', None) | ('ok', redirect_url).
    Caller loops: on 'captcha' solve then call login() again."""
    j = _post_form(sess, f"{tt.SSO}/account_login/v3/", {
        "mix_mode": "1", "aid": tt.AID_SELLER, "service": tt.SSO_SERVICE, "language": tt.LANGUAGE,
        "account": xor_hex(email), "password": xor_hex(password),
    }, verify_fp, ms_token, csrf)
    ec = j.get("error_code")
    if ec == 1107 or j.get("verify_center_decision_conf"):
        conf = json.loads(j["verify_center_decision_conf"])
        if not conf.get("fp"):
            conf["fp"] = verify_fp
        logger.info("[login captcha] type=%s subtype=%s region=%s", conf.get("type"),
                    conf.get("subtype"), conf.get("region"))
        return "captcha", conf
    if ec == 1039:
        return "otp", None
    if ec == 0:
        return "ok", j.get("redirect_url")
    raise ConnectError(f"account_login/v3 error_code={ec}: {j.get('description') or json.dumps(j)[:200]}")


# --------------------------------------------------------------------------- captcha (SSE-relayed)
# The login page answers its bdturing conf through verify-sg with the 2.x SDK: plain JSON, no
# msToken or X-Bogus/X-Gnarly on these calls. Parameters, their order and the body are the
# ones Chrome sent on a real sub-account login (24 Sep 2026; tiktok_shop_constants CAPTCHA_LOGIN_*).
def _captcha_query(sess, conf: dict) -> dict:
    dev = _dev(sess)
    region = conf.get("region") or "sg"
    aid = str(conf.get("aid") or tt.AID_SELLER)
    app_name = str(conf.get("app_name") or (tt.APP_NAME_AFFILIATE if aid == tt.AID_AFFILIATE else tt.CAPTCHA_LOGIN_APP_NAME))
    subtype = conf.get("subtype") or "3d"
    challenge_code = str(conf.get("challenge_code") or tt.CAPTCHA_CHALLENGE_CODE.get(subtype, tt.CAPTCHA_CHALLENGE_CODE["3d"]))
    return {
        "lang": dev.language.split("-")[0], "app_name": app_name, "h5_sdk_version": tt.CAPTCHA_LOGIN_H5_SDK,
        "h5_sdk_use_type": "cdn", "sdk_version": "", "iid": "0", "did": "0", "device_id": "0",
        "ch": "web_text", "aid": aid, "os_type": "2", "mode": subtype, "tmp": str(int(time.time() * 1000)),
        "platform": "pc", "webdriver": "false", "fp": conf.get("fp"),
        "type": conf.get("type") or "verify", "detail": conf.get("detail", ""),
        "server_sdk_env": conf.get("server_sdk_env", ""), "imagex_domain": "",
        "subtype": subtype, "challenge_code": challenge_code,
        "os_name": {"MacIntel": "mac", "Win32": "windows"}.get(dev.platform, "linux"),
        "h5_check_version": tt.CAPTCHA_LOGIN_CHECK_VERSION, "region": region, "triggered_region": region,
        "cookie_enabled": "true", "screen_width": str(dev.screen[0]), "screen_height": str(dev.screen[1]),
        "browser_language": dev.language, "browser_platform": dev.platform, "browser_name": "Mozilla",
        "browser_version": dev.browser_version,
    }


def _captcha_url(path: str, q: dict) -> str:
    return f"{tt.CAPTCHA_LOGIN_HOST}{path}?{_browser_urlencode(q)}"


def fetch_captcha(sess, conf: dict) -> Dict[str, Any]:
    """GET a fresh challenge ('3d' click or 'slide' puzzle) for the login decision conf."""
    hdr = _ua(sess, referer=f"{tt.SELLER}/", origin=tt.SELLER, accept="application/json, text/plain, */*")
    gr = sess.get(_captcha_url("/captcha/get", _captcha_query(sess, conf)), headers=hdr)
    try:
        g = gr.json()
    except Exception:
        g = None
    if not isinstance(g, dict) or not g.get("data"):
        raise ConnectError(f"captcha/get {gr.status_code}: {gr.text[:200]}")
    data = g["data"]
    ch = (data.get("challenges") or [{}])[0]
    ques = ch.get("question", {})
    img_url = ques.get("url1")
    if not (ch.get("id") and img_url):
        raise ConnectError(f"captcha/get no challenge: {json.dumps(g)[:200]}")

    mode = ch.get("mode") or conf.get("subtype") or "3d"
    img = sess.get(img_url, headers=_ua(sess)).content

    piece_bytes = None
    piece_url = ques.get("url2")
    if piece_url:
        try:
            piece_bytes = sess.get(piece_url, headers=_ua(sess)).content
        except Exception:
            pass

    return {
        "id": ch["id"],
        "verify_id": data.get("verify_id"),
        "mode": mode,
        "challenge_code": ch.get("challenge_code"),
        "question": ques.get("ques", "Geser puzzle ke posisi yang sesuai" if mode == "slide" else "Pilih 2 objek yang identik"),
        "img_url": img_url,
        "img_bytes": img,
        "piece_url": piece_url,
        "piece_bytes": piece_bytes,
        "tip_y": ques.get("tip_y", 0),
    }


def _verified(v: dict) -> bool:
    return (v.get("msg_sub_code") == "success"
            or v.get("message") in ("Verifikasi selesai", "Verification complete")
            or (v.get("code") == 200 and v.get("data") is None))


def _is_drag(mode: str, pts) -> bool:
    return mode in ("slide", "whirl") or (mode not in ("3d", "icon", "select") and len(pts) == 1)


def _norm_points(points_native):
    """Accept any FE shape -> list of (x,y) floats. Supports [[x,y],..], [x,y], [{x,y}], [num], num."""
    if isinstance(points_native, (int, float)):
        return [(float(points_native), 0.0)]
    pts = []
    for p in (points_native or []):
        if isinstance(p, dict):
            pts.append((float(p.get("x", 0)), float(p.get("y", 0))))
        elif isinstance(p, (list, tuple)):
            pts.append((float(p[0]), float(p[1]) if len(p) > 1 else 0.0))
        elif isinstance(p, (int, float)):
            pts.append((float(p), 0.0))
    return pts


def submit_captcha(sess, conf: dict, challenge: Dict[str, Any],
                   points_native, img_native_w: int, now_ms: int) -> bool:
    """Submit ANY captcha subtype dynamically. The FE renders whatever image(s)/question TikTok
    returned and reports the user's interaction as points (native px); this maps them to the
    verify-sg `reply` per subtype:
      - drag types (slide / whirl): 1 point = the drag END -> synthesised eased trajectory 0..end.
      - click types (3d / icon / select): N points -> one reply entry per click, in order.
    Unknown subtypes fall back to: 1 point => drag, else => click list. So new TikTok captcha
    types keep working as long as the user can interact with the rendered challenge."""
    mode = challenge.get("mode", conf.get("subtype") or "3d")
    pts = _norm_points(points_native)
    if not pts:
        raise ConnectError("captcha: no interaction points provided")
    scale = float(tt.CAPTCHA_LOGIN_IMG_W) / (img_native_w or 552)

    if _is_drag(mode, pts):
        tx, ty = pts[0]
        sx = round(tx * scale, 4)
        if mode == "slide" and challenge.get("tip_y") is not None:
            sy = float(challenge["tip_y"])
        else:
            sy = round((ty or challenge.get("tip_y", 0)) * scale, 4)

        total_time = random.randint(700, 1000)
        end_time = now_ms - random.randint(80, 150)
        start_time = end_time - total_time
        steps = random.randint(24, 32)
        reply = []
        for s in range(steps):
            p = s / (steps - 1)
            e = p * p * (3 - 2 * p)  # easeInOut
            t_s = start_time + int(p * total_time)
            reply.append({"x": round(sx * e, 4),
                          "y": round(sy, 4),
                          "time": t_s})
    else:
        # The clicks happened before the verify, ~0.8 s apart (the browser stamps each click).
        start = now_ms - 400 - 800 * len(pts)
        reply = [{"x": round(x * scale, 4), "y": round(y * scale, 4),
                  "time": start + i * 800 + random.randint(0, 120)} for i, (x, y) in enumerate(pts)]

    one = {"id": challenge["id"], "modified_img_width": tt.CAPTCHA_LOGIN_IMG_W, "mode": mode, "reply": reply,
           "models": {}, "reply2": [], "models2": {}, "events": "{\"userMode\":0}"}
    body = {"modified_img_width": tt.CAPTCHA_LOGIN_IMG_W, "id": challenge["id"], "mode": mode, "reply": reply,
            "models": {}, "log_params": {}, "reply2": [], "models2": {}, "version": 2,
            "verify_id": challenge["verify_id"], "verify_requests": [one], "events": "{\"userMode\":0}"}

    body_str = json.dumps(body, separators=(",", ":"))
    hdr = _ua(sess, referer=f"{tt.SELLER}/", origin=tt.SELLER, accept="application/json, text/plain, */*",
              **{"content-type": "application/json"})
    sub_q = _captcha_query(sess, conf)
    if challenge.get("challenge_code"):
        sub_q["challenge_code"] = str(challenge["challenge_code"])
    try:
        v = sess.post(_captcha_url("/captcha/verify", sub_q),
                      data=body_str, headers=hdr).json()
    except Exception:
        return False
    ok = _verified(v)
    (logger.info if ok else logger.warning)(
        "[login captcha] verify mode=%s ok=%s code=%s msg_sub_code=%s", mode, ok,
        v.get("code"), v.get("msg_sub_code"))
    return ok


# --------------------------------------------------------------------------- OTP
def send_otp(sess, email: str, verify_fp: str, ms_token: str, csrf: str) -> None:
    s = _post_form(sess, f"{tt.SSO}/send_email_activate_code/v2/", {
        "mix_mode": "1", "aid": tt.AID_SELLER, "service": tt.SSO_SERVICE, "language": tt.LANGUAGE,
        "email": xor_hex(email), "ect_type": tt.LOGIN_ECT_TYPE,
    }, verify_fp, ms_token, csrf)
    if s.get("error_code") != 0:
        raise ConnectError(tt_error(s, "Pengiriman kode verifikasi"))


def submit_otp(sess, email: str, code: str, verify_fp: str, ms_token: str, csrf: str) -> str:
    a = _post_form(sess, f"{tt.SSO}/activate_email/code_login/", {
        "mix_mode": "1", "aid": tt.AID_SELLER, "service": tt.SSO_SERVICE, "language": tt.LANGUAGE,
        "email": xor_hex(email), "code": xor_hex(code), "ect_type": tt.LOGIN_ECT_TYPE,
    }, verify_fp, ms_token, csrf)
    if a.get("error_code") != 0 or not a.get("redirect_url"):
        raise ConnectError(f"code_login failed: {json.dumps(a)[:200]}")
    return a["redirect_url"]


# --------------------------------------------------------------------------- finalize / export
def finalize(sess, redirect_url: str, verify_fp: str, ms_token: str, csrf: str) -> Dict[str, Any]:
    """Follow sso callback, confirm session, mint seller & affiliate context. Returns account+shop metadata."""
    sess.get(redirect_url, headers=_ua(sess))
    verify_fp = ck(sess, "s_v_web_id") or verify_fp
    ms_token = ck(sess, "msToken") or ms_token
    csrf = ck(sess, "passport_csrf_token") or csrf

    info_url = _signed(sess, f"{tt.SELLER}/passport/account/info/v2/",
                       {"get_info_type": "2", "aid": tt.AID_SELLER, "account_sdk_source": "web",
                        "sdk_version": tt.PASSPORT_SDK_LOGIN, "language": tt.LANGUAGE},
                       "", verify_fp, ms_token)
    data = sess.get(info_url, headers=_base_headers(sess, csrf)).json().get("data", {})
    if not data.get("user_id"):
        raise ConnectError(f"account/info did not confirm session: {json.dumps(data)[:200]}")

    # Device cookies (ttwid + s_v_web_id) are minted later by _ensure_device_cookies
    # (jsdom secsdk) in refresh_cookies_pipeline — no per-bind ttwid curl call needed here.

    # 1. Enter seller homepage once -> mints oec_seller_id_* + seller session cookies.
    try:
        sess.get(tt.SSO_SERVICE, headers=_ua(sess))
    except Exception:
        pass

    # seller_id/region from cookies is enough for the caller's mismatch check; shop_name + im_shop_id
    # are resolved authoritatively by verify_account_info inside refresh_cookies_pipeline, so we don't
    # duplicate those affiliate API calls here (faster bind).
    seller_id = (ck(sess, "oec_seller_id_unified_seller_env")
                 or ck(sess, "global_seller_id_unified_seller_env") or ck(sess, "oec_seller_id"))
    region = (ck(sess, "store-country-code") or "ID").upper()
    im_shop_id = ck(sess, "SHOP_ID") or ck(sess, "im_shop_id")

    return {"user_id": str(data["user_id"]), "email": data.get("email"), "name": data.get("name"),
            "seller_id": seller_id, "im_shop_id": im_shop_id, "shop_name": None, "region": region}


def export_netscape(sess) -> str:
    """Serialize the jar (tokopedia.com only) to Netscape format for refresh_cookies_pipeline."""
    out = {}
    for c in sess.cookies.jar:
        if c.value and c.domain and (tt.COOKIE_DOMAIN.lstrip(".") in c.domain or "tiktok" in c.domain):
            out[(c.domain, c.name)] = {
                "domain": c.domain, "path": c.path or "/", "secure": bool(c.secure),
                "expires": int(c.expires) if c.expires else 0, "value": c.value,
            }
    return write_netscape(out)


