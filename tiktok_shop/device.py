"""The device a TikTok session presents: UA, TLS target, client hints, and what the page's
SDKs read from the browser (platform, screen, languages, timezone, canvas).

TikTok ties a session to a device -- its cookies (s_v_web_id, msToken, ttwid), the UA and
TLS fingerprint, and the navigator its SDKs fingerprint -- so every request, signature, SDK
runner and login step of one session must present the same Device.

  * legacy()         -- the identity GrowSeller shops presented before per-shop devices;
  * generate(seed)   -- a realistic desktop, stable per seed (new email binds);
  * from_navigator() -- the importing browser (cookie imports);
  * from_fp()        -- a stored Device.to_fp() dict.
"""
import random
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from curl_cffi.requests import BrowserType

from . import constants as tt

DEFAULT_CANVAS = 1135188161

_CHROME_TARGETS = sorted(
    (int(m.group(1)), b.value) for b in BrowserType
    if (m := re.fullmatch(r"chrome(\d+)a?", b.value)))


@dataclass(frozen=True)
class Device:
    user_agent: str
    platform: str                          # navigator.platform: MacIntel / Win32 / Linux x86_64
    screen: Tuple[int, int, int, int]      # width, height, availWidth, availHeight
    language: str
    languages: Tuple[str, ...]
    timezone: str
    canvas: int
    hardware_concurrency: int = 8
    device_memory: int = 8
    sec_ch_ua: Optional[str] = None        # None: not a Chromium browser, send no client hints
    session_nonce: Optional[int] = None    # X-Gnarly field 8, fixed per page session
    source: str = "legacy"

    @property
    def chrome_major(self) -> Optional[int]:
        m = re.search(r"Chrome/(\d+)", self.user_agent)
        return int(m.group(1)) if m else None

    @property
    def sec_ch_ua_platform(self) -> str:
        return {"MacIntel": '"macOS"', "Win32": '"Windows"'}.get(self.platform, '"Linux"')

    @property
    def impersonate(self) -> str:
        """The newest curl_cffi Chrome target not newer than this UA, so the TLS/HTTP2
        fingerprint belongs to the browser the UA claims."""
        major = self.chrome_major or 131
        fit = [target for version, target in _CHROME_TARGETS if version <= major]
        return fit[-1] if fit else "chrome131"

    @property
    def accept_language(self) -> str:
        """Accept-Language as Chrome builds it from navigator.languages."""
        langs = list(self.languages[:5]) or [self.language]
        return ",".join(lang if i == 0 else f"{lang};q={1 - i / 10:.1f}" for i, lang in enumerate(langs))

    @property
    def browser_version(self) -> str:
        return self.user_agent.split("/", 1)[1] if "/" in self.user_agent else self.user_agent

    def navigator(self) -> Dict[str, Any]:
        """As the page's SDKs read it (fork runner, jsdom minter)."""
        w, h, aw, ah = self.screen
        return {"userAgent": self.user_agent, "platform": self.platform,
                "language": self.language, "languages": list(self.languages),
                "hardwareConcurrency": self.hardware_concurrency, "deviceMemory": self.device_memory,
                "maxTouchPoints": 0, "timezone": self.timezone,
                "screen": {"width": w, "height": h, "availWidth": aw, "availHeight": ah}}

    def to_fp(self) -> Dict[str, Any]:
        out = asdict(self)
        out["screen"] = list(self.screen)
        out["languages"] = list(self.languages)
        return out


# ------------------------------------------------------------------ constructors

def legacy() -> Device:
    """The identity every shop presented before per-shop devices: Mac, Chrome 131 UA,
    client hints and TLS (tiktok_shop_constants LEGACY_*), fixed so shops that still
    present it never move onto another device."""
    return Device(user_agent=tt.LEGACY_USER_AGENT, platform="MacIntel", screen=(1536, 960, 1536, 867),
                  language="en-US", languages=("en-US", "en", "id"), timezone=tt.TIMEZONE,
                  canvas=DEFAULT_CANVAS, sec_ch_ua=tt.LEGACY_SEC_CH_UA, source="legacy")


def _chrome_ua(os_part: str, major: int) -> str:
    return f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def _sec_ch_ua(major: int, grease: str) -> str:
    return f'"Chromium";v="{major}", "{grease}";v="24", "Google Chrome";v="{major}"'


# Common desktops of Indonesian sellers: (platform, os part of the UA, screens as
# (w, h, availH)). Chrome versions stay at ones curl_cffi can impersonate, so TLS matches.
_DESKTOPS = [
    ("MacIntel", "Macintosh; Intel Mac OS X 10_15_7",
     [(1440, 900, 875), (1512, 982, 944), (1536, 960, 935), (1680, 1050, 1025), (1728, 1117, 1079)]),
    ("Win32", "Windows NT 10.0; Win64; x64",
     [(1366, 768, 728), (1536, 864, 824), (1920, 1080, 1040), (1600, 900, 860), (1280, 720, 680)]),
]
_MAJORS = [v for v, _ in _CHROME_TARGETS if v >= 142] or [131]


def generate(seed: str) -> Device:
    """A realistic, stable device for `seed` (the same seed always gives the same one)."""
    rnd = random.Random(f"growseller-device:{seed}")
    platform, os_part, screens = rnd.choice(_DESKTOPS)
    w, h, ah = rnd.choice(screens)
    major = rnd.choice(_MAJORS)
    language = rnd.choice(["id-ID", "en-US"])
    languages = ("id-ID", "id", "en-US", "en") if language == "id-ID" else ("en-US", "en", "id")
    return Device(user_agent=_chrome_ua(os_part, major), platform=platform, screen=(w, h, w, ah),
                  language=language, languages=languages, timezone=tt.TIMEZONE,
                  canvas=rnd.randrange(1 << 28, 1 << 32),
                  hardware_concurrency=rnd.choice([4, 8, 8, 12, 16]), device_memory=8,
                  sec_ch_ua=_sec_ch_ua(major, rnd.choice(["Not=A?Brand", "Not_A Brand", "Not.A/Brand"])),
                  source="generated")


def _num(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def _text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] if text.isprintable() else ""


def from_navigator(nav: Mapping[str, Any], seed: str) -> Optional[Device]:
    """The importing browser, from what its page reported. None when it does not look
    like a desktop browser (then the caller falls back to generate())."""
    if not isinstance(nav, Mapping):
        return None
    ua = _text(nav.get("userAgent"), 512)
    if not ua.startswith("Mozilla/5.0") or re.search(r"Mobile|Android|iPhone|iPad", ua):
        return None
    screen = nav.get("screen") if isinstance(nav.get("screen"), Mapping) else {}
    w = _num(screen.get("width"), 800, 7680, 1536)
    h = _num(screen.get("height"), 600, 4320, 960)
    languages = tuple(x for x in (_text(v, 35) for v in (nav.get("languages") or [])[:8]) if x)
    language = _text(nav.get("language"), 35) or (languages[0] if languages else "id-ID")
    brands = _text(nav.get("secChUa"), 256)
    return Device(
        user_agent=ua,
        platform=_text(nav.get("platform"), 32) or "Win32",
        screen=(w, h, _num(screen.get("availWidth"), 800, w, w), _num(screen.get("availHeight"), 500, h, h)),
        language=language, languages=languages or (language,),
        timezone=_text(nav.get("timezone"), 64) or tt.TIMEZONE,
        # The browser's own canvas hash is not readable here; a stable one per import.
        canvas=random.Random(f"growseller-canvas:{seed}").randrange(1 << 28, 1 << 32),
        hardware_concurrency=_num(nav.get("hardwareConcurrency"), 1, 128, 8),
        device_memory=_num(nav.get("deviceMemory"), 1, 64, 8),
        sec_ch_ua=brands or None,
        source="browser",
    )


def from_fp(fp: Optional[Mapping[str, Any]]) -> Device:
    """A stored device_fp; anything it lacks is the legacy identity's."""
    base = legacy()
    if not fp or not fp.get("user_agent"):
        return replace(base, canvas=int((fp or {}).get("canvas") or base.canvas),
                       session_nonce=_opt_int((fp or {}).get("session_nonce")))
    screen = tuple(int(x) for x in (fp.get("screen") or base.screen))[:4]
    return Device(
        user_agent=str(fp["user_agent"]),
        platform=str(fp.get("platform") or base.platform),
        screen=screen if len(screen) == 4 else base.screen,
        language=str(fp.get("language") or base.language),
        languages=tuple(fp.get("languages") or base.languages),
        timezone=str(fp.get("timezone") or base.timezone),
        canvas=int(fp.get("canvas") or base.canvas),
        hardware_concurrency=int(fp.get("hardware_concurrency") or 8),
        device_memory=int(fp.get("device_memory") or 8),
        sec_ch_ua=fp.get("sec_ch_ua") if "sec_ch_ua" in fp else base.sec_ch_ua,
        session_nonce=_opt_int(fp.get("session_nonce")),
        source=str(fp.get("source") or "stored"),
    )


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["Device", "legacy", "generate", "from_navigator", "from_fp", "DEFAULT_CANVAS"]
