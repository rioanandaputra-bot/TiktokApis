"""The Affiliate Center's bdturing captcha (h5 SDK 3.x "oec-captcha-ttweb"), solved headless.

/captcha/get returns an AES-encrypted challenge and the answer goes to /captcha/verifyV2 as an
encrypted protobuf `captchaBody`; that crypto lives in a bdsword VM TikTok does not let us
reimplement, so the SDK itself decrypts and encrypts, run in jsdom by js/captcha_oracle.js
(no browser). This module orchestrates: get -> decrypt -> find the slide gap (OpenCV edge
template matching) -> synthesise the drag -> encrypt -> verifyV2.

Calls to the captcha host are unsigned: X-Bogus/X-Gnarly there are refused ([5013]).
"""
import io
import json
import logging
import math
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from . import constants as tt
from .device import Device
from .web import browser_urlencode, client_hints

logger = logging.getLogger("tiktok_shop.captcha")

ORACLE_JS = Path(__file__).resolve().parent / "js" / "captcha_oracle.js"


def find_slide_puzzle_position(bg_bytes: bytes, piece_bytes: bytes, tip_y: Optional[int] = None) -> Tuple[int, int, float]:
    """Locate the (x, y) of the puzzle notch in the background. Canny edges + TM_CCOEFF_NORMED on the
    alpha-masked piece; when tip_y is known, constrain the vertical search band. Returns (x, y, score)
    in background-image pixel coordinates."""
    bg = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
    piece = np.array(Image.open(io.BytesIO(piece_bytes)).convert("RGBA"))

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_RGB2GRAY)
    piece_gray = cv2.cvtColor(piece[:, :, :3], cv2.COLOR_RGB2GRAY)
    alpha = piece[:, :, 3]

    bg_edges = cv2.Canny(cv2.GaussianBlur(bg_gray, (3, 3), 0), 100, 200)
    piece_edges = cv2.Canny(cv2.GaussianBlur(piece_gray, (3, 3), 0), 100, 200)
    piece_edges = cv2.bitwise_and(piece_edges, piece_edges, mask=alpha)

    if tip_y is not None and tip_y > 0:
        y_552 = int(round(tip_y * 552.0 / 288.0))
        y_start = max(0, y_552 - 35)
        y_end = min(bg.shape[0], y_552 + piece.shape[0] + 35)
        band = bg_edges[y_start:y_end, :]
        res = cv2.matchTemplate(band, piece_edges, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= 0.5:
            return int(max_loc[0]), int(y_start + max_loc[1]), float(max_val)

    res = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return int(max_loc[0]), int(max_loc[1]), float(max_val)


def run_oracle(payload: Dict[str, Any], timeout_s: int = 45) -> str:
    """Execute the Node captcha oracle with stdin JSON and return stdout. The SDK build and the
    page it runs on come from tiktok_shop.constants unless the payload names them."""
    payload = {"sdk": tt.CAPTCHA_V2_SDK, "page_url": tt.AFFILIATE_MARKETPLACE_PAGE, **payload}
    proc = subprocess.run(["node", str(ORACLE_JS)], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=timeout_s, cwd=str(ORACLE_JS.parent))
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"oracle {payload.get('op')} failed: {(proc.stderr or '')[:200]}")
    return proc.stdout


def get_query(conf: Dict[str, Any], ms_token: str, device: Device) -> Dict[str, str]:
    """The /captcha/get query for an Affiliate Center bdturing conf (fp/detail/server_sdk_env must
    be the conf's own, or TikTok answers a junk challenge verifyV2 then rejects with [5011])."""
    ext = conf.get("extension") if isinstance(conf.get("extension"), dict) else {}
    h5_sdk_ver, _, sdk_ver = (ext.get("setting_version") or tt.CAPTCHA_V2_SDK).partition("-")
    now = time.time()
    # shark_log_id: the SDK stamps <UTC YYYYMMDDHHMMSS><upper hex>; tmp: request epoch-ms.
    shark_log_id = time.strftime("%Y%m%d%H%M%S", time.gmtime(now)) + "%018X" % random.getrandbits(72)
    q = {
        "lang": "en", "app_name": "", "h5_sdk_version": h5_sdk_ver, "h5_sdk_use_type": "cdn",
        "sdk_version": sdk_ver, "iid": "0", "did": "0", "device_id": "0", "ch": "web_text",
        "aid": str(conf.get("aid") or tt.AID_AFFILIATE), "os_type": "2", "tmp": str(int(now * 1000)),
        "platform": "pc", "webdriver": "false", "shark_log_id": shark_log_id,
        "refer_path": conf.get("refer_path", tt.CAPTCHA_AFFILIATE_REFER_PATH),
        "fp": conf.get("fp") or "", "type": conf.get("type") or "verify",
        "detail": conf.get("detail", ""), "server_sdk_env": conf.get("server_sdk_env", ""),
        "subtype": conf.get("subtype") or "slide",
        "challenge_code": str(conf.get("challenge_code") or tt.CAPTCHA_AFFILIATE_CHALLENGE_CODE),
        "os_name": {"MacIntel": "mac", "Win32": "windows"}.get(device.platform, "linux"),
        "h5_check_version": sdk_ver, **tt.CAPTCHA_GET_CANARIES,
    }
    if ms_token:
        q["msToken"] = ms_token
    return q


def build_reply(drag_x: int, bg_w: int) -> Tuple[List[Dict[str, Any]], int, float]:
    """Synthesise the VerifyRequest reply: an organic human-like drag trajectory 0..drag_x, scaled from
    background-image pixels to the displayed slider width, featuring realistic velocity curves, muscle
    micro-jitter, vertical drift arc, natural overshoot, and settle dwell."""
    scale = tt.CAPTCHA_V2_IMG_W / float(bg_w or 552)
    end_x = round(drag_x * scale, 2)
    total_ms = random.randint(850, 1150)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - total_ms - random.randint(50, 120)

    # 1. Generate variable dt time steps matching realistic mousemove pacing (~60fps)
    times = [0]
    cur_t = random.randint(25, 45)  # initial reaction hesitation
    times.append(cur_t)
    while cur_t < total_ms - 75:
        cur_t += random.randint(14, 23)
        times.append(cur_t)
    if times[-1] < total_ms:
        times.append(total_ms)

    steps = len(times)
    overshoot = random.uniform(1.8, 3.2)
    overshoot_step = int(steps * random.uniform(0.78, 0.85))
    peak_x = end_x + overshoot
    y_arc_peak = random.uniform(-2.2, 2.5)

    reply = []
    prev_x = 0.0

    for i, t in enumerate(times):
        if i == 0:
            reply.append({
                "x": 0.0,
                "y": 0.0,
                "relative_time": 0,
                "time": start_ms,
            })
            continue

        if i == steps - 1:
            reply.append({
                "x": end_x,
                "y": round(random.gauss(0, 0.2), 2),
                "relative_time": total_ms,
                "time": start_ms + total_ms,
            })
            continue

        p = i / float(steps - 1)

        # X progression with organic acceleration, overshoot, and fine correction
        if i <= overshoot_step:
            prog = i / float(overshoot_step)
            # Cubic ease-in-out S-curve
            e = prog * prog * (3 - 2 * prog)
            target_x = peak_x * e
            jitter = random.gauss(0, 0.25)
            x_val = max(prev_x + 0.1, target_x + jitter)
        else:
            corr_prog = (i - overshoot_step) / float(steps - 1 - overshoot_step)
            target_x = peak_x - (peak_x - end_x) * (corr_prog ** 0.8)
            jitter = random.gauss(0, 0.18)
            x_val = target_x + jitter

        prev_x = x_val

        # Y progression: wrist rotation arc + physiological micro-tremor
        y_arc = math.sin(p * math.pi) * y_arc_peak
        y_jitter = random.gauss(0, 0.35)
        y_val = round(y_arc + y_jitter, 2)

        reply.append({
            "x": round(x_val, 2),
            "y": y_val,
            "relative_time": t,
            "time": start_ms + t,
        })

    return reply, total_ms, end_x


def _verified(v: Dict[str, Any]) -> bool:
    return (v.get("msg_sub_code") == "success"
            or v.get("message") in ("Verification complete", "Verifikasi selesai")
            or (v.get("code") == 200 and v.get("data") is None))


def solve_slide(conf: Dict[str, Any], device: Device, cookies: Iterable[Tuple[str, str, str, str]],
                ms_token: str = "", max_attempts: int = 3) -> bool:
    """Solve the slide challenge for `conf` as `device`, with the session's cookies
    ((name, value, domain, path) tuples). True only when TikTok answers verification complete."""
    from curl_cffi import requests as cffi_requests
    max_attempts = max(1, min(int(max_attempts), 3))
    sess = cffi_requests.Session(impersonate=device.impersonate)
    for name, value, domain, path in cookies:
        if value:
            sess.cookies.set(name, value, domain=domain, path=path or "/")
    ms_token = conf.get("msToken") or ms_token or ""
    hdr = {"user-agent": device.user_agent, "origin": tt.AFFILIATE, "referer": f"{tt.AFFILIATE}/",
           "accept": "application/json, text/plain, */*", "accept-language": device.accept_language,
           **client_hints(device),
           "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "cross-site"}
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                q = get_query(conf, ms_token, device)
                gr = sess.get(f"{tt.CAPTCHA_AFFILIATE_HOST}/captcha/get?{browser_urlencode(q)}", headers=hdr)
                try:
                    data = (gr.json() or {}).get("data")
                except Exception:
                    data = None
                if isinstance(data, dict):
                    challenge = data                        # plaintext answer, no bdsword envelope
                elif isinstance(data, str) and data:
                    challenge = json.loads(run_oracle({"op": "decrypt", "data": data, "config": q}))
                else:
                    logger.warning("[captcha] get %s gave no challenge (attempt %d)", gr.status_code, attempt)
                    continue
                challenge.setdefault("uuid", int(time.time() * 1000))
                question = challenge.get("question") or {}
                if challenge.get("mode") != "slide" or not question.get("url2"):
                    logger.warning("[captcha] unsupported challenge mode=%s", challenge.get("mode"))
                    return False

                bg = sess.get(question["url1"], headers={"user-agent": device.user_agent}).content
                piece = sess.get(question["url2"], headers={"user-agent": device.user_agent}).content
                bg_w = Image.open(io.BytesIO(bg)).width
                x, _, score = find_slide_puzzle_position(bg, piece, tip_y=question.get("tip_y"))
                reply, total_ms, end_x = build_reply(x, bg_w)
                body = json.dumps({"captchaBody": run_oracle({
                    "op": "encrypt", "challenge": challenge, "uuid": challenge.get("uuid"),
                    "reply": reply, "drag_width": tt.CAPTCHA_V2_DRAG_W,
                    "modified_img_width": tt.CAPTCHA_V2_IMG_W, "duration": total_ms,
                    "ua": device.user_agent, "referer": f"{tt.AFFILIATE}/",
                    "location": f"{tt.AFFILIATE}{q['refer_path']}",
                    "h5_sdk_version": q["h5_sdk_version"], "sdk_version": q["sdk_version"],
                }).strip()}, separators=(",", ":"))

                # verifyV2 adds mode + the xx-tt-dd canary and drops the get canaries;
                # challenge_code must be the decrypted challenge's own.
                vq = {k: v for k, v in q.items() if k not in tt.CAPTCHA_GET_CANARIES}
                vq.update(challenge_code=str(challenge.get("challenge_code") or tt.CAPTCHA_CHALLENGE_CODE["slide"]),
                          mode=challenge.get("mode") or q["subtype"], subtype=q["subtype"],
                          tmp=str(int(time.time() * 1000)), **tt.CAPTCHA_VERIFY_CANARIES)
                vr = sess.post(f"{tt.CAPTCHA_AFFILIATE_HOST}/captcha/verifyV2?{browser_urlencode(vq)}",
                               data=body, headers={**hdr, "content-type": "application/json"})
                try:
                    v = vr.json()
                except Exception:
                    v = {}
                ok = _verified(v)
                (logger.info if ok else logger.warning)(
                    "[captcha] slide attempt %d/%d x=%d score=%.2f end_x=%.1f -> %s %s", attempt,
                    max_attempts, x, score, end_x, vr.status_code, v.get("msg_sub_code") or v.get("message"))
                if ok:
                    return True
            except Exception as e:
                logger.warning("[captcha] attempt %d/%d failed: %s", attempt, max_attempts, str(e)[:200])
            if attempt < max_attempts:
                time.sleep(1.0)
        return False
    finally:
        try:
            sess.close()
        except Exception:
            pass
