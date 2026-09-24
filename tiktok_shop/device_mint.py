"""Headless (Node + jsdom, NO browser/chromium) mint of TikTok device cookies.

Affiliate 'sensitive' endpoints (api_sens .../cmp/contact — creator phone/email) require a valid
`s_v_web_id` that TikTok's secsdk generates CLIENT-SIDE. curl_cffi can't run JS and a
hand-made value is rejected. So we run the REAL secsdk in jsdom (pure JS DOM — no chromium, no
node-canvas/cairo) via `js/mint_svwebid.js`, which loads the real affiliate page and reads the
device cookies it writes. Verified: the secsdk output works from curl_cffi immediately.

This is the light, headless, SaaS-scalable replacement for a Playwright/chromium browser. The mint
runs once per shop (s_v_web_id is long-lived + reused across all creators), not per request.
"""
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import constants as tt
from .device import legacy

logger = logging.getLogger("tiktok_shop.device_mint")

MINT_JS = Path(__file__).resolve().parent / "js" / "mint_svwebid.js"
# cookie -> the domain the browser scopes it to (must match for the signed affiliate calls)
_DOMAINS = {
    "s_v_web_id": tt.AFFILIATE_DOMAIN,
    "user_oec_info": tt.AFFILIATE_DOMAIN,
    "msToken": tt.COOKIE_DOMAIN,
    "ttwid": tt.COOKIE_DOMAIN,
    "odin_tt": tt.COOKIE_DOMAIN,
}


def mint_device_cookies(cookie_items: List[dict], timeout_s: int = 45,
                        device: Optional[dict] = None) -> Dict[str, dict]:
    """cookie_items: [{name,value,domain,path?}]. Runs the jsdom secsdk minter and returns
    {name: {domain,path,value}} for the device cookies it produced. Empty dict on any failure
    (node/jsdom missing, timeout) so callers fall back to the cheap curl ttwid mint.

    The trigger page and wait ceiling are constants SECSDK_MINT_URL / SECSDK_MINT_WAIT_MS.
    Timing is logged so hit/miss + duration are measurable per mint."""
    body = {
        "cookies": [{"name": c["name"], "value": c["value"], "domain": c["domain"],
                     "path": c.get("path", "/")} for c in cookie_items if c.get("value")],
        # The minted device must be the one every signed request will claim: the shop's.
        "device": device or legacy().navigator(),
    }
    body["url"] = tt.SECSDK_MINT_URL
    body["waitMs"] = tt.SECSDK_MINT_WAIT_MS
    payload = json.dumps(body)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(["node", str(MINT_JS)], input=payload, capture_output=True,
                              text=True, timeout=timeout_s, cwd=str(MINT_JS.parent))
        data = json.loads(proc.stdout or "{}")
        if not data and proc.stderr:
            logger.warning("[device_mint] jsdom mint empty; stderr: %s", proc.stderr[:200])
    except Exception as e:
        logger.warning("[device_mint] jsdom mint failed after %.2fs: %s", time.monotonic() - t0, e)
        return {}
    logger.warning("[device_mint] jsdom mint took %.2fs, s_v_web_id=%s (url=%s)",
                   time.monotonic() - t0, bool((data or {}).get("s_v_web_id")), tt.SECSDK_MINT_URL)

    result: Dict[str, dict] = {}
    for name, val in (data or {}).items():
        if val:
            dom = _DOMAINS.get(name, tt.COOKIE_DOMAIN)
            result[name] = {"domain": dom, "path": "/", "value": val}
    return result
