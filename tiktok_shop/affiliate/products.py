"""The shop's products as the Open collaboration page lists them.

`open_collaboration/promote_products/list` with no search params is every product in the
open plan (the "Added products" tab; 143 on the live shop 24 Sep 2026), each with its open
commission and how many creators showcase or post it. Products outside the plan ("Not added")
come from product_selection/list with is_promoted=false -- the same endpoint as the
invitation picker (invitations.product_selection).
"""
from typing import Any, Dict, List, Optional

from .. import constants as tt
from ..client import ok_data
from . import ShopApi

PAGE_SIZE = 50


def referer(api: ShopApi) -> str:
    return (f"{tt.AFFILIATE}/affiliate/collaboration/open-collaboration"
            f"?shop_id={api.seller_id or ''}&shop_region={api.region}")


def _call(api: ShopApi, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    return ok_data(api.signed("POST", f"{tt.AFFILIATE_API}{path}", q, body, api.headers(referer(api))), path)


def open_collab_page(api: ShopApi, page: int) -> Dict[str, Any]:
    """One page of products in the open plan: {products: [product()], total}."""
    res = _call(api, "/affiliate/open_collaboration/promote_products/list",
                {"shop_id": str(api.seller_id or ""), "search_params": [], "cur_page": page,
                 "page_size": PAGE_SIZE})
    return {"products": [open_collab_product(p) for p in res.get("products") or []],
            "total": int(res.get("total_num") or 0)}


def not_in_open_collab_page(api: ShopApi, page: int) -> Dict[str, Any]:
    """One page of the shop's products outside the open plan: {products: [product()], total}."""
    res = _call(api, "/affiliate/product_selection/list", {
        "cur_page": page, "page_size": PAGE_SIZE, "source": 1,
        "search_params": [{"key": 4, "is_promoted": False, "search_type": 2},
                          {"key": 13, "search_type": 2, "value": "0"}]})
    return {"products": [product(p) for p in res.get("products") or []],
            "total": int(res.get("total_num") or 0)}


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def product(p: Dict[str, Any]) -> Dict[str, Any]:
    """A base product (product_selection item or promote_products' base_product), flat."""
    price = p.get("price") or {}
    stock = p.get("stock")
    if stock is None and isinstance(p.get("stock_info"), dict):
        stock = p["stock_info"].get("spu_stock")
    return {
        "product_id": str(p.get("product_id") or ""),
        "title": p.get("title"),
        "image_url": p.get("image_url"),
        "price_min": _int(price.get("min_price")) if isinstance(price, dict) else None,
        "price_max": _int(price.get("max_price")) if isinstance(price, dict) else None,
        "stock": _int(stock),
        "status": _int(p.get("status")),
    }


def open_collab_product(item: Dict[str, Any]) -> Dict[str, Any]:
    """A promote_products item: the base product plus its open-plan numbers."""
    ap = item.get("affiliate_product") or {}
    out = product(ap.get("base_product") or {})
    rate = (ap.get("current_commission_rate") or {}).get("commission_rate")
    fs = item.get("free_sample_rule") or {}
    out.update({
        "open_status": _int(item.get("status")),
        "open_commission_rate": _int(rate),             # hundredths of a percent: 800 = 8%
        "open_showcase_creators": _int(item.get("showcase_creator_count")) or 0,
        "open_content_creators": _int(item.get("video_live_creator_count")) or 0,
        "open_free_sample": bool(fs.get("offer_free_sample")),
    })
    return out


def from_promotion_detail(base: Dict[str, Any]) -> Dict[str, Any]:
    """creator_promotion_detail's product_base_info in the same shape (no image kept: the
    drawer's image is a signed list)."""
    price = (base.get("price") or {})
    lo = ((price.get("min_price") or {}).get("price"))
    hi = ((price.get("max_price") or {}).get("price"))
    return {"product_id": str(base.get("product_id") or ""), "title": base.get("title"),
            "image_url": None, "price_min": _int(lo), "price_max": _int(hi),
            "stock": _int(base.get("stock")), "status": _int(base.get("product_status"))}


def all_pages(fetch, api: ShopApi, max_pages: int = 40) -> List[Dict[str, Any]]:
    """Every product a paged fetch returns."""
    out: List[Dict[str, Any]] = []
    for n in range(1, max_pages + 1):
        res = fetch(api, n)
        out += [p for p in res["products"] if p["product_id"]]
        if len(out) >= res["total"] or not res["products"]:
            break
    return out
