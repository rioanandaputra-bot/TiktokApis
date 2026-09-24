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

# Where a request came from (`source_type`), with the page's own "Request channel" wording:
# allow_search_source_types of affiliate/sample/tab/list, live 24 Sep 2026. 0, 1 and 7 are the
# three kinds of collaboration; 6 appears in real data but the page does not name it.
SOURCE_TYPES = {
    0: "Open collaboration",
    1: "Target collaboration",
    3: "Partner campaign",
    5: "TikTok Shop campaign",
    7: "Collaboration Plus",
}
COLLABORATION_SOURCES = (0, 1, 7)

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


# apply_info fields that belong to the shop's master data (creator, product, SKU) or are
# already columns of the request row; everything else stays with the request as `detail`.
_NOT_DETAIL = ("apply_id", "curr_status", "create_time", "product_id", "sku_id", "source_type",
               "product_title", "sku_desc", "sku_image", "sku_price", "sku_stock", "region")


def creator_of(info: Dict[str, Any]) -> Dict[str, Any]:
    """The creator a request came from, as shop master data. Its top videos (the bulk of the
    payload, with signed URLs) are not kept; the avatar URL is signed and expires within
    days, so it is refreshed by every sync rather than trusted forever."""
    return {
        "creator_id": str(info.get("creator_id") or ""),
        "handle": info.get("name"),
        "nickname": info.get("nick_name"),
        "avatar_url": info.get("avatar_url"),
        "follower_num": _as_int(info.get("follower_num")),
        "ecom_level": _as_int(info.get("ecom_level")),
        "fulfillment_rate": info.get("fulfillment_rate"),
    }


def rows_from(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten TikTok's creator-grouped payload into one row per request.

    Each row: the request's own columns (apply_id, curr_status, request_created_at,
    creator_id, product_id, sku_id, source_type), its remaining fields as `detail`, and the
    master data it mentions -- `creator`, `product` {product_id, title} and `sku` -- for the
    caller to keep once per shop rather than once per request.
    """
    out: List[Dict[str, Any]] = []
    for agg in payload.get("agg_info") or []:
        creator = creator_of((agg.get("apply_group") or {}).get("creator_info") or {})
        # "apply_deatil" is TikTok's spelling; do not correct it.
        for a in ((agg.get("apply_deatil") or {}).get("apply_infos") or []):
            if not a.get("apply_id"):
                continue
            pid, sku = str(a.get("product_id") or ""), str(a.get("sku_id") or "")
            price = (a.get("sku_price") or {}).get("price_value")
            out.append({
                "apply_id": str(a["apply_id"]),
                "curr_status": int(a.get("curr_status") or 0),
                "request_created_at": _as_int(a.get("create_time")),
                "creator_id": creator["creator_id"] or None,
                "product_id": pid or None,
                "sku_id": sku or None,
                "source_type": _as_int(a.get("source_type")),
                "detail": {k: v for k, v in a.items() if k not in _NOT_DETAIL},
                "creator": creator,
                "product": {"product_id": pid, "title": a.get("product_title")},
                "sku": {"sku_id": sku, "product_id": pid, "sku_desc": a.get("sku_desc"),
                        "image_url": a.get("sku_image"), "price": _as_int(price),
                        "stock": _as_int(a.get("sku_stock"))},
            })
    return out


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
