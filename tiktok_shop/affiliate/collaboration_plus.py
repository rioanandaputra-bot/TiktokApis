"""Collaboration Plus (affiliate/sea_content/recruitment_activity*), the second tab of the
Target collaboration page.

A different API family from invitation_group: programs with tasks and platform rewards,
eleven statuses (0-10). Verified 24 Sep 2026 on a shop with no programs, so only the two
calls its landing page makes are known: whether the shop may create one, and how many
programs it has per status. The program list and the status names wait for a shop that
has programs.
"""
from typing import Any, Dict

from .. import constants as tt
from ..client import decode_body
from . import ShopApi

BODY = {"version": "1", "locale": "en"}


def referer(api: ShopApi) -> str:
    return (f"{tt.AFFILIATE}/affiliate/collaboration/target-invitation"
            f"?shop_region={api.region}&shop_id={api.seller_id or ''}&tab=2")


def _call(api: ShopApi, path: str) -> Dict[str, Any]:
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    res = decode_body(api.signed("POST", f"{tt.AFFILIATE_API}{path}", q, dict(BODY), api.headers(referer(api))))
    if not isinstance(res, dict) or res.get("code") != 0:
        msg = res.get("msg") or res.get("message") if isinstance(res, dict) else "unreadable body"
        raise RuntimeError(f"{path} failed: code={res.get('code') if isinstance(res, dict) else '?'} {msg}")
    return res


def can_create(api: ShopApi) -> bool:
    return bool(_call(api, "/affiliate/sea_content/recruitment_activity/check_can_create").get("can_create"))


def program_counts(api: ShopApi) -> Dict[int, int]:
    """{status_code: programs} for the eleven statuses."""
    data = _call(api, "/affiliate/sea_content/recruitment_activity_program/get_program_num_by_status").get("data") or {}
    return {int(k): int(v or 0) for k, v in (data.get("program_num_by_status") or {}).items()}
