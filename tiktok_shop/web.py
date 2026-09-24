"""Request details a Chromium page sends: query encoding and client hints."""
import urllib.parse
from typing import Any, Dict

from .device import Device


def browser_urlencode(params: Dict[str, Any]) -> str:
    """URL-encode like the page's own query builder: brackets and commas stay literal."""
    qs = urllib.parse.urlencode(params)
    return qs.replace("%28", "(").replace("%29", ")").replace("%2C", ",")


def client_hints(device: Device) -> Dict[str, str]:
    """sec-ch-ua* as a Chromium browser sends them; none for Firefox/Safari."""
    if not device.sec_ch_ua:
        return {}
    return {"sec-ch-ua": device.sec_ch_ua, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": device.sec_ch_ua_platform}
