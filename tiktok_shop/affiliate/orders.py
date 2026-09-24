"""Affiliate orders (the Payments > Affiliate orders page, /product/order).

The same page opens from an invitation's "View orders" button with `plan_id` set to the
invitation id, which the body carries as affiliate_seller_search_condition.plan_id (live page
24 Sep 2026). Each order's promotion_position_info.promotion_id is NOT the invitation id --
two creators of one invitation carried different ones -- so filter by plan_id, never match
on promotion_id.

Paging is by cursor: the answer's next_cursor goes back as `cursor`. The page always sends an
order_create_time window (its default is the last 90 days).
"""
from typing import Any, Dict, List, Optional

from .. import constants as tt
from ..client import ok_data
from . import ShopApi

PATH = "/api/oec/pay/statement/order/seller/orders/list"     # not under /api/v1
PAGE_SIZE = 50

# campaign_type: the page's two tabs.
CREATORS = 1        # Affiliate creator
PARTNERS = 2        # Affiliate partner


def referer(api: ShopApi, plan_id: str = "") -> str:
    tail = f"plan_id={plan_id}&enter_from=target_invitation_detail_page&" if plan_id else ""
    return f"{tt.AFFILIATE}/product/order?{tail}shop_region={api.region}&shop_id={api.seller_id or ''}"


def page(api: ShopApi, start_ms: int, end_ms: int, plan_id: str = "", cursor: str = "",
         campaign_type: int = CREATORS, size: int = PAGE_SIZE) -> Dict[str, Any]:
    """One page: {orders: [...], total, has_more, next_cursor}."""
    cond: Dict[str, Any] = {"cod_type": 0, "campaign_type": campaign_type, "op_assist_order_type": 2,
                            "order_create_time": {"start_time": int(start_ms), "end_time": int(end_ms)}}
    if plan_id:
        # A JSON number here: a string is refused ("expected int") on our requests, though the
        # page's own body shows it quoted.
        cond["plan_id"] = int(plan_id)
    body: Dict[str, Any] = {"query_size": size, "page_size": size, "affiliate_seller_search_condition": cond}
    if cursor:
        body["cursor"] = cursor
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    raw = api.signed("POST", f"{tt.AFFILIATE}{PATH}", q, body, api.headers(referer(api, plan_id)))
    data = ok_data(raw, "orders/list").get("data") or {}
    return {"orders": [order(o) for o in data.get("sku_order_list") or []],
            "total": _int(data.get("total_count")), "has_more": bool(data.get("has_more")),
            "next_cursor": data.get("next_cursor") or ""}


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _amount(m: Any) -> Optional[int]:
    """A money object's amount in the currency's unit (IDR has no minor unit)."""
    if isinstance(m, dict) and m.get("amount") not in (None, ""):
        return _int(m.get("amount"))
    return None


def order(o: Dict[str, Any]) -> Dict[str, Any]:
    """One SKU order line, flattened to what a seller reads."""
    base = o.get("sku_order_base_info_for_affiliate_seller") or {}
    com = o.get("sku_order_commission_info_for_affiliate_seller") or {}
    info = base.get("sku_order_info") or {}
    creator = base.get("creator_info") or {}
    product = base.get("product_info") or {}
    promo = info.get("promotion_position_info") or {}
    return {
        "order_id": str(info.get("main_order_id") or ""),
        "sku_id": str(info.get("sku_id") or ""),
        "product_id": str(product.get("product_id") or ""),
        "product_name": product.get("product_name"),
        "creator_id": str(creator.get("creator_oec_id") or ""),
        "creator_handle": creator.get("creator_username"),
        "created_at": _int(info.get("create_time")) or None,
        "quantity": _int(info.get("sale_quantity")),
        "sale_price": _amount(info.get("sale_price")),
        "settlement_status": _int(info.get("settlement_status")),
        "is_cod": bool(info.get("is_cod")),
        "refund_number": _int(info.get("refund_number")),
        "return_number": _int(info.get("return_number")),
        "promotion_type": _int(promo.get("promotion_type")) or None,
        "est_commission_base": _amount(com.get("est_commission_base")),
        "actual_commission_base": _amount(com.get("actual_commission_base")),
        "est_commission": _amount(com.get("est_standard_commission")),
        "actual_commission": _amount(com.get("actual_standard_commission")),
        "commission_rate": _int(com.get("standard_cos_ratio")) or None,
    }


def summarize(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals over order lines: orders, units, GMV and commission."""
    return {
        "orders": len({o["order_id"] for o in orders if o["order_id"]}),
        "units": sum(o["quantity"] for o in orders),
        "gmv": sum((o["sale_price"] or 0) for o in orders),
        "est_commission": sum((o["est_commission"] or 0) for o in orders),
    }
