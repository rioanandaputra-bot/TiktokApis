"""Sample requests (affiliate/sample/group/list), as the Sample Requests page asks for them.

One call returns a page of creators with every request inlined
(`agg_info[].apply_deatil.apply_infos[]` -- the typo is TikTok's), so a walk is one request per
50 creators. The status a seller cares about is each request's `curr_status`, a wider set than
the page's seven tabs. Verified against the live page on 23 Sep 2026.
"""
from typing import Any, Dict, List, Optional, Tuple

from .. import constants as tt
from ..client import ok_data
from . import ShopApi

PAGE_SIZE = 50          # what its own page asks for

# Buckets for the page's left column, in lifecycle order. Each maps to the raw
# `curr_status` codes that belong to it.
#
# EVERY code listed here was read off the live page on 23 Sep 2026: the response was
# matched to the creator row rendering it, and the chip beside that row is the label
# below. Nothing here is inferred from the name of a status or the order of a tab -- an
# earlier pass guessed 53 as "Video fulfilled" and it turned out to be "Overdue".
#
# The labels with no codes are the ones the mockup asks for that this shop has never
# been in. They render at zero rather than being mapped on a hunch; when a code for one
# shows up it arrives in `unmapped_codes` from counts() and gets added here.
#
# "Overdue" is not in the mockup but is real, so it is here.
STATUSES: List[Tuple[str, str, Tuple[int, ...]]] = [
    ("to_review",            "To Review",            (10,)),
    ("ready_to_ship",        "Ready to Ship",        ()),
    ("shipped",              "Shipped",              ()),
    ("shipment_delayed",     "Shipment Delayed",     ()),
    ("content_pending",      "Content Pending",      (40,)),
    ("video_not_confirmed",  "Video Not Confirmed",  ()),
    ("live_not_confirmed",   "LIVE Not Confirmed",   ()),
    ("video_fulfilled",      "Video Fulfilled",      ()),
    ("live_fulfilled",       "LIVE Fulfilled",       ()),
    ("completed",            "Completed",            (100,)),
    ("completed_no_content", "Completed No Content", ()),
    ("overdue",              "Overdue",              (53,)),
    ("unfulfilled",          "Unfulfilled",          ()),
    ("rejected",             "Rejected",             (51,)),
    # 55 and 56 both print "Canceled" on TikTok's page; they differ only in
    # sample_order_type (18 vs 10) and approval_source (4 vs 1), not in what the seller
    # is told. Proven by alinaherlia_: its three code-55 requests are exactly the three
    # rows the page marks Canceled, and its other two (code 52) are the Expired ones.
    ("canceled",             "Canceled",             (55, 56)),
    ("expired",              "Expired",              (52,)),
]

# Every code the buckets above claim. Anything else is "Others" — see the module note.
_KNOWN = {code for _, _, codes in STATUSES for code in codes}


def bucket_of(curr_status: int) -> str:
    for key, _, codes in STATUSES:
        if curr_status in codes:
            return key
    return "others"


def referer(api: ShopApi) -> str:
    return f"{tt.AFFILIATE}/affiliate/sample/sample-request?shop_region={api.region}&shop_id={api.seller_id or ''}"


def call(api: ShopApi, path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST") -> Dict[str, Any]:
    """One call as the Sample Requests page makes it; the decoded answer (code 0) or RuntimeError."""
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    return ok_data(api.signed(method, f"{tt.AFFILIATE_API}{path}", q, body, api.headers(referer(api))), path)


def fetch_page(api: ShopApi, page: int) -> Dict[str, Any]:
    """One page of creators with their requests inlined, exactly as its page asks."""
    return call(api, "/affiliate/sample/group/list", {
        "tab": 0,                       # 0 = All; the narrower tabs are a subset
        "cur_page": page,
        "page_size": PAGE_SIZE,
        "search_params": [{"search_key": 1, "search_type": 2, "value": ""}],
        "order_params": [],
    })


def rows_from(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten TikTok's creator-grouped payload into one row per request.

    Only the four searchable values are lifted out; the request keeps its whole
    apply_info, with the creator it belongs to folded in under `creator`, so the page
    can render any field without this function having to know about it.
    """
    out: List[Dict[str, Any]] = []
    for agg in payload.get("agg_info") or []:
        creator = (agg.get("apply_group") or {}).get("creator_info") or {}
        # "apply_deatil" is TikTok's spelling; do not correct it.
        for a in ((agg.get("apply_deatil") or {}).get("apply_infos") or []):
            if not a.get("apply_id"):
                continue
            out.append({
                "apply_id": str(a["apply_id"]),
                "curr_status": int(a.get("curr_status") or 0),
                "request_created_at": _as_int(a.get("create_time")),
                "raw": dict(a, creator=creator),
            })
    return out


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
