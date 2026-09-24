"""Affiliate Center endpoints the chat page (seller/im) uses besides the IM host itself: the IM
token, a creator's contact, invitations and products to attach, creator profiles, image upload.
Queries keep the parameter order the page sends -- the signatures cover it.
"""
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .. import constants as tt
from ..client import decode_body
from . import ShopApi

SESSION_EXPIRED = 98001002        # "You must log in to continue": TikTok revoked the session
CONTACT_RATE_LIMITED = 16005003   # the sensitive contact endpoint's own limit (cool down ~1 min)
CONTACT_REFUSED = (10000, 10005, 40001, 98001004)


class UploadError(RuntimeError):
    pass


def im_referer(api: ShopApi, query: Optional[Dict[str, str]] = None) -> str:
    q = query or api.common_query()
    return (f"{tt.AFFILIATE_IM_PAGE}?enter_from=nav_im_entry&shop_region={q.get('shop_region', tt.REGION)}"
            f"&shop_id={api.seller_id or ''}")


def _chat_headers(api: ShopApi, region: str, query: Dict[str, str]) -> Dict[str, str]:
    h = api.headers(im_referer(api, query))
    h["x-tt-oec-region"] = region
    return h


def _pairs(first: List[Tuple[str, str]], common: Dict[str, str]) -> List[Tuple[str, str]]:
    """The page's own parameters first, then the common query (without repeating a key)."""
    seen = dict(first)
    return first + [(k, v) for k, v in common.items() if k not in seen]


# ============================================================================ token

def im_token(api: ShopApi) -> Any:
    """The shop's IM token answer ({data: {token, user, app_key, ws_url, env, ...}}), decoded."""
    return decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/oec/affiliate/seller/im/get/token",
                                  api.common_query(), None, api.headers(tt.AFFILIATE_IM_PAGE)))


# ============================================================================ contact

def contact(api: ShopApi, creator_oec_id: str, scene: int = 11) -> Any:
    """A creator's contact (api_sens/.../cmp/contact), decoded. A sensitive endpoint: pace calls
    per shop and treat CONTACT_RATE_LIMITED / CONTACT_REFUSED / SESSION_EXPIRED as the caller must."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    pairs = _pairs([("creator_oecuid", str(creator_oec_id)), ("shop_id", str(api.seller_id or "")),
                    ("shop_region", region), ("scene", str(scene))], q)
    headers = _chat_headers(api, region, q)
    headers["accept-language"] = "id,en-US;q=0.9,en;q=0.8"
    return decode_body(api.signed("GET", f"{tt.AFFILIATE}/api_sens/v1/affiliate/cmp/contact", pairs, None, headers))


def contact_values(res: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """{phone, email} from a contact answer (field 1 phone, field 2 email)."""
    phone = email = None
    for item in res.get("contact_info", []) or []:
        if isinstance(item, dict) and item.get("value"):
            if item.get("field") == 1:
                phone = str(item["value"]).strip()
            elif item.get("field") == 2:
                email = str(item["value"]).strip()
    return {"phone": phone, "email": email}


# ============================================================================ invitations

def invitation_detail(api: ShopApi, invitation_id: str, creator_oec_id: Optional[str] = None) -> Any:
    """A target invitation as the chat's invitation card shows it (lux/invitation/detail)."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    first = {"invitation_id": str(invitation_id), "creator_oec_id": str(creator_oec_id or ""),
             "invitation_group_id": "", "shop_region": region, "oec_region": region,
             "oec_seller_id": str(api.seller_id or "")}
    query = {**first, **{k: v for k, v in q.items() if k not in first}}
    return decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/affiliate/lux/invitation/detail", query, None,
                                  _chat_headers(api, region, q)))


def available_invitations(api: ShopApi, creator_oec_id: str, query: str = "", page: int = 1,
                          page_size: int = 10) -> Any:
    """Target invitations the shop can attach in a chat with this creator."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    pairs = _pairs([("creator_oec_id", str(creator_oec_id)), ("key_words", str(query)),
                    ("page_size", str(page_size)), ("cur_page", str(page)), ("shop_region", region),
                    ("oec_region", region), ("oec_seller_id", str(api.seller_id or ""))], q)
    return decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/affiliate/lux/invitation/available_list", pairs,
                                  None, _chat_headers(api, region, q)))


def auth_profiles(api: ShopApi, creator_oec_ids: List[str]) -> Any:
    """Authorized profile metadata (avatar, handle, nickname, followers, categories) of creators."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    headers = _chat_headers(api, region, q)
    headers["content-type"] = "application/json"
    return decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/affiliate/lux/creator/auth_profiles", q,
                                  {"creator_oec_ids": [str(c) for c in creator_oec_ids]}, headers))


# ============================================================================ products

def _commission_text(raw: Any) -> str:
    """600 -> "6% commission rate" (TikTok sends basis points)."""
    if raw is None or raw == "":
        return ""
    try:
        n = float(raw) / 100.0
        return f"{int(n) if n.is_integer() else n}% commission rate"
    except Exception:
        return f"{raw}% commission rate"


def _range_text(r: Dict[str, Any]) -> Tuple[str, Any, Any]:
    lo = r.get("min_price_format") or r.get("min_price", "")
    hi = r.get("max_price_format") or r.get("max_price", "")
    return (f"{lo} - {hi}" if lo and hi and lo != hi else (lo or hi or "")), lo, hi


def _plan_name(plan_type: Any) -> str:
    return {1: "Open collaboration", 2: "Target collaboration"}.get(plan_type, "Affiliate collaboration")


def _plan_product(d: Dict[str, Any]) -> Dict[str, Any]:
    """A product from im/product/info or im/product/list, as a chat product card shows it."""
    earn = (d.get("earn_commission_range") or {})
    earn_str = earn.get("min_price_format") or earn.get("min_price")
    if earn_str and not str(earn_str).lower().startswith("earn"):
        earn_str = f"Earn {earn_str}"
    price, lo, hi = _range_text(d.get("sale_price_range") or {})
    sale_num = str(d.get("sale_num", "0"))
    image = d.get("image_url", "")
    return {"product_id": str(d.get("product_id", "")), "title": d.get("title", ""), "name": d.get("title", ""),
            "image_url": image, "image": image, "cover_url": image, "price": price, "min_price": lo,
            "max_price": hi, "earn": earn_str or "", "sold": f"{sale_num} sold", "sales": sale_num,
            "commission_rate": _commission_text(d.get("commission_rate")) if d.get("commission_rate") else "",
            "commission_rate_raw": d.get("commission_rate"), "plan_type": d.get("plan_type"),
            "plan_type_name": _plan_name(d.get("plan_type")), "plan_id": str(d.get("plan_id", "")),
            "status": d.get("product_status", 1)}


def product_info(api: ShopApi, product_id: str, creator_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A product card's details (oec/affiliate/seller/im/product/info), or None when TikTok has none."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    pairs = _pairs([("creator_id", str(creator_id or "")), ("product_id", str(product_id).strip()),
                    ("shop_region", region), ("oec_region", region), ("oec_seller_id", str(api.seller_id or ""))], q)
    res = decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/oec/affiliate/seller/im/product/info", pairs, None,
                                 _chat_headers(api, region, q)))
    if not (isinstance(res, dict) and res.get("code") == 0 and res.get("data")):
        return None
    d = res["data"]
    item = _plan_product(d)
    item.update(sale_price_format=item["price"], sale_num=item["sales"], plan_status=d.get("plan_status", 1),
                product_status=d.get("product_status", 1))
    item.pop("status", None)
    item.pop("sales", None)
    return item


def creator_products(api: ShopApi, creator_id: str, page: int = 1, page_size: int = 10, query: str = "",
                     search_key: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Products the shop can attach in a chat with this creator (im/product/list):
    {product_list, total}, or None when TikTok answered nothing usable."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    first = [("creator_id", str(creator_id)), ("cur_page", str(page)), ("page_size", str(page_size))]
    query = str(query or "").strip()
    if query:
        # search_key 1 = product id, 2 = name (a 10+ digit query is an id)
        key = str(search_key) if search_key in (1, 2, "1", "2") else ("1" if query.isdigit() and len(query) >= 10 else "2")
        first += [("search_key", key), ("key_word", query)]
    first += [("shop_region", region), ("oec_region", region), ("oec_seller_id", str(api.seller_id or ""))]
    res = decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/oec/affiliate/seller/im/product/list",
                                 _pairs(first, q), None, _chat_headers(api, region, q)))
    if not (isinstance(res, dict) and res.get("code") in (0, None) and res.get("data")):
        return None
    data = res["data"]
    raw = data.get("product_plan_info") or data.get("product_list") or []
    return {"product_list": [_plan_product(d) for d in raw], "total": data.get("total", len(raw))}


def open_collaboration_products(api: ShopApi, page: int = 1, page_size: int = 10, query: str = "",
                                search_key: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The shop's Open Collaboration products (open_collaboration/promote_products/list), for a
    message with no creator yet: {product_list, total}, or None."""
    q = api.common_query()
    region = q.get("shop_region", tt.REGION)
    seller_id = str(api.seller_id or "")
    query = str(query or "").strip()
    if query:
        # search_key 1 = name, 2 = product id (listProductOpenCollab.har)
        if search_key in (2, "2") or (not search_key and (not query.isdigit() or len(query) < 10)):
            params = [{"search_key": 1, "search_value": query}, {"search_key": 10, "search_value": "", "status": 1}]
        else:
            params = [{"search_key": 2, "search_value": query}]
    else:
        params = [{"search_key": 10, "search_value": "", "status": 1}]
    body = {"shop_id": seller_id, "search_params": params, "cur_page": page, "page_size": page_size}
    pairs = _pairs([("user_language", "en"), ("oec_seller_id", seller_id), ("shop_region", region)], q)
    headers = api.headers(f"{tt.AFFILIATE}/affiliate/collaboration/open-collaboration?shop_id={seller_id}&shop_region={region}")
    headers["x-tt-oec-region"] = region
    headers["content-type"] = "application/json"
    res = decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/affiliate/open_collaboration/promote_products/list",
                                 pairs, body, headers))
    if not (isinstance(res, dict) and res.get("code") in (0, None) and "products" in res):
        return None
    out = []
    for wrap in res.get("products") or []:
        ap = wrap.get("affiliate_product") or {}
        bp = ap.get("base_product") or {}
        est = ap.get("estimate_commission") or {}
        lo_e, hi_e = est.get("min_price_format") or "", est.get("max_price_format") or ""
        earn = f"Earn {lo_e} - {hi_e}" if lo_e and hi_e and lo_e != hi_e else (f"Earn {lo_e}" if lo_e else "")
        price, lo, hi = _range_text(bp.get("sale_price") or bp.get("price") or {})
        sale_num = str(bp.get("sales", "0"))
        rate = (ap.get("current_commission_rate") or {}).get("commission_rate")
        image = bp.get("image_url", "")
        out.append({"product_id": str(bp.get("product_id", "")), "title": bp.get("title", ""),
                    "name": bp.get("title", ""), "image_url": image, "image": image, "cover_url": image,
                    "price": price, "min_price": lo, "max_price": hi, "earn": earn, "sold": f"{sale_num} sold",
                    "sales": sale_num, "commission_rate": _commission_text(rate), "commission_rate_raw": rate,
                    "plan_type": 1, "plan_type_name": "Open collaboration",
                    "plan_id": str((ap.get("contract") or {}).get("contract_id") or ""), "status": bp.get("status", 1)})
    return {"product_list": out, "total": res.get("total_num", len(out))}


# ============================================================================ upload

def upload_image(api: ShopApi, image_bytes: bytes, filename: str = "image.png",
                 content_type: str = "image/png") -> Dict[str, Any]:
    """Upload an image for a chat message: {url, key, width, height}. Multipart like the page's
    FormData upload, so the signatures cover an empty body."""
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"images[]\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n").encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
    q = api.common_query()
    headers = {"content-type": f"multipart/form-data; boundary={boundary}", "origin": tt.AFFILIATE,
               "referer": tt.AFFILIATE_IM_PAGE, "x-tt-oec-region": q.get("shop_region", tt.REGION)}
    res = decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/im/shop_creator/shop/multimedia/image/upload",
                                 q, body, headers, sign_body=False))
    if isinstance(res, dict) and res.get("code") == 0:
        details = (res.get("data") or {}).get("image_details") or []
        if details:
            img = details[0]
            return {"url": img.get("url"), "key": img.get("key"), "width": img.get("width", 1024),
                    "height": img.get("height", 1024)}
    raise UploadError(res.get("message") if isinstance(res, dict) else "Upload failed")
