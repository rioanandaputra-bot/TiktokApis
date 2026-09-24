"""X-Bogus and X-Gnarly for a request, as the page's webmssdk stamps them.

Both come from the upstream pure encoders (signing/pure.py): ~3 ms a pair, byte-identical to
TikTok's JS for the same inputs (checked 24 Sep 2026). Each signs the exact query AND body the
request sends -- a POST must pass its body, the signature carries md5(md5(body)) -- with the
session's device: its UA and canvas, and X-Gnarly field 8 is the device's page-session nonce
when it has one.
"""
import threading
import time

from . import constants as tt
from ._upstream import pure
from .device import Device

# The browser stamps X-Bogus and X-Gnarly from ONE clock read (decoded 23 Sep 2026: both carried
# second 1790179077). Callers sign X-Bogus then X-Gnarly for the same query and body, so the
# second call reuses the first one's timestamp instead of risking a second boundary between.
_clock = threading.local()


def x_bogus(query: str, body: str, device: Device) -> str:
    ts_ms = int(time.time() * 1000)
    _clock.pending = (query, body or "", ts_ms, device)
    return pure.encode_x_bogus(query, device.user_agent, body or "", timestamp=ts_ms // 1000,
                               ubcode=tt.SIGN_MODE, magic=device.canvas)


def x_gnarly(query: str, body: str, device: Device) -> str:
    pending = getattr(_clock, "pending", None)
    _clock.pending = None
    if pending and pending[:2] == (query, body or "") and pending[3] == device \
            and time.time() * 1000 - pending[2] < 2000:
        ts_ms = pending[2]
    else:
        ts_ms = int(time.time() * 1000)
    nonce = device.session_nonce
    return pure.encode_gnarly_project(query, body or "", device.user_agent, ubcode=tt.SIGN_MODE,
                                      canvas=device.canvas, timestamp=ts_ms // 1000,
                                      timestamp_ms=nonce if nonce is not None else ts_ms)


def sign_query(query: str, body: str, device: Device) -> str:
    """`query` with X-Bogus and X-Gnarly appended, in the page's order."""
    return f"{query}&X-Bogus={x_bogus(query, body, device)}&X-Gnarly={x_gnarly(query, body, device)}"
