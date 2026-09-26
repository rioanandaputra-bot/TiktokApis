"""Target Collaboration invitations (oec/affiliate/seller/invitation_group/*) and the product
picker their create page uses.

Bodies, enums and limits are the ones TikTok's own pages send and enforce, read from their
bundles and verified against live calls (23-24 Sep 2026) unless noted. The detail endpoint
checks X-Bogus, X-Gnarly and X-Tts-Oec-Bsid as one set, which ShopApi.signed provides.
"""
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .. import constants as tt
from ..client import decode_body, ok_data
from . import ShopApi

PAGE_SIZE = 50          # what the list page asks for
MAX_PAGE_SIZE = tt.INVITATION_SEARCH_MAX_PAGE_SIZE     # the most search accepts, for a full walk

# Status tabs and the codes count/search answer with (live page 23 Sep 2026: the tab labels
# read Ongoing 9, Expiring 0, Canceling 0, Completed 88 and count returned exactly those).
STATUSES: List[Tuple[str, str, int]] = [
    ("ongoing",   "Ongoing",   1),
    ("expiring",  "Expiring",  2),
    ("canceling", "Canceling", 3),
    ("completed", "Completed", 4),
]
COMPLETED = 4
OPEN_STATUS_CODES = (1, 2, 3)          # creators can be added, the invite edited or ended
EDITABLE_STATUS_CODES = (1, 2)
SEARCH_GROUP_TYPES = [1, 5, 3, 2, 9, 7]

# rate_limit_info's `reason` (SendLimitReason_* in the bundle). The create page asks this before
# opening the form; 98001004 on create cannot tell these apart.
LIMIT_REASONS = {
    1: "TikTok's 24-hour limit for creating invitations is reached; try again tomorrow",
    2: "TikTok's maximum of open invitations is reached; end or wait out an ongoing one first",
}

# Group name: an over-long one is refused with 98001004 -- the same code as the active-group cap,
# so it once read as a rate limit. Every name that ever succeeded was 29 characters or fewer.
MAX_NAME = 30
MAX_MESSAGE = 500

# ContentType (bundle): NoPrefer=0, Video=1, Live=2. Live needs live-auction enabled.
CONTENT_OPTIONS = {"NO_PREFERENCE": 0, "VIDEO": 1, "LIVE": 2}


def status_label(code: int) -> str:
    for _, label, c in STATUSES:
        if c == code:
            return label
    return f"Status {code}"


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ============================================================================ calls

def list_referer(api: ShopApi) -> str:
    return (f"{tt.AFFILIATE}/affiliate/collaboration/target-invitation"
            f"?shop_region={api.region}&shop_id={api.seller_id or ''}&tab=1")


def create_referer(seller_id: str) -> str:
    return (f"{tt.AFFILIATE}/connection/target-invitation/create?enter_from=target_invitation_list"
            f"&shop_region={tt.REGION}&shop_id={seller_id}")


def call(api: ShopApi, path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST") -> Dict[str, Any]:
    """One invitation_group call as the list page makes it (its referer, user_language id-ID);
    the answer's `data`, or RuntimeError when TikTok did not answer code 0."""
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    raw = api.signed(method, f"{tt.AFFILIATE_API}{path}", q, body, api.headers(list_referer(api)))
    return ok_data(raw, path).get("data") or {}


# The list page's search (live pages 24 and 26 Sep 2026): one box with a field select --
# Invitation name, Invitation ID, Product name, Product ID -- and a creator box whose pick is
# sent as the creator's oec id. Each is one query item {type, key}; names match loosely.
# The list has no sorting: it comes newest first and the page offers no sort control.
QUERY_TYPES = {"product_id": 1, "product_name": 2, "invitation_id": 3, "name": 4, "creator_id": 6}
QUERY_NAME = QUERY_TYPES["name"]


# The list page's two filter chips (live page 26 Sep 2026): "Accepted invitations" sends
# filter_accept_status 2 instead of 3 (all), "With free samples" adds free_sample_status 1.
ACCEPT_ALL, ACCEPT_ONLY = 3, 2


def _search_params(query: Any = None, accepted: bool = False, with_sample: bool = False) -> Dict[str, Any]:
    """`query`: {field of QUERY_TYPES: text}, or a bare string for a name search.
    `accepted` / `with_sample`: the page's two filters."""
    if isinstance(query, str):
        query = {"name": query}
    items = [{"type": QUERY_TYPES[field], "key": str(key).strip()}
             for field, key in (query or {}).items() if str(key or "").strip()]
    params = {"query_items": items, "filter_accept_status": ACCEPT_ONLY if accepted else ACCEPT_ALL,
              "search_group_type": list(SEARCH_GROUP_TYPES)}
    if with_sample:
        params["free_sample_status"] = 1
    return params


def search(api: ShopApi, status_code: int, page: int, query: Any = None,
           page_size: int = PAGE_SIZE, accepted: bool = False, with_sample: bool = False) -> Dict[str, Any]:
    """One page of invitations in one status, optionally narrowed by `query` and the page's
    two filters (see _search_params): {invitation_list, total, has_more}. page_size at most
    MAX_PAGE_SIZE."""
    return call(api, "/oec/affiliate/seller/invitation_group/search", {
        "search_params": _search_params(query, accepted, with_sample), "invitation_group_status": status_code,
        "page_size": min(page_size, MAX_PAGE_SIZE), "cur_page": page})


def find_creators(api: ShopApi, handle: str, size: int = 20) -> List[Dict[str, Any]]:
    """The creator box's suggestions for what was typed (search/creator, query_by 1 = handle):
    [{id, handle, nickname}]. The list is then searched by the picked creator's id."""
    data = call(api, "/oec/affiliate/seller/invitation_group/search/creator", {
        "size": size, "search_id": "0", "query": handle.strip().lstrip("@"), "query_by": 1})
    return [{"id": str(c["creator_oec_id"]), "handle": c.get("user_name"), "nickname": c.get("nick_name")}
            for c in data.get("creators") or [] if c.get("creator_oec_id")]


def items(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The invitations in one search page, as TikTok sent them."""
    return [it for it in (page.get("invitation_list") or []) if it.get("id")]


def counts_of(item: Dict[str, Any]) -> Dict[str, int]:
    """The numbers a search item already carries, so the list needs no detail call:
    products, creators invited, creators who added a product to their showcase (Accepted),
    creators who posted content (Promoted)."""
    return {"products": _as_int(item.get("product_cnt")) or 0,
            "invited": _as_int(item.get("creator_cnt")) or 0,
            "accepted": _as_int(item.get("creator_added_cnt")) or 0,
            "promoted": _as_int(item.get("creator_posted_cnt")) or 0}


def counts(api: ShopApi, name: str = "") -> Dict[int, int]:
    """TikTok's own per-status totals {status_code: count}, optionally for a name search."""
    data = call(api, "/oec/affiliate/seller/invitation_group/count", {
        "search_params": _search_params(name), "invitation_status": [c for _, _, c in STATUSES]})
    rows = data if isinstance(data, list) else (data.get("data") or [])
    return {int(r["invitation_group_status"]): int(r.get("count") or 0)
            for r in rows if isinstance(r, dict) and "invitation_group_status" in r}


def send_block(api: ShopApi) -> Optional[str]:
    """Why TikTok would refuse a new invitation right now, or None if it would not."""
    data = call(api, "/oec/affiliate/seller/invitation_group/rate_limit_info", {})
    if data.get("can_invite", True):
        return None
    reason = _as_int(data.get("reason"))
    return LIMIT_REASONS.get(reason, f"TikTok refuses new invitations (reason {reason})")


# ============================================================================ quota and limits
#
# Read per shop, never assumed: TikTok sends them per shop, so a shop may have its own.

def quota(api: ShopApi) -> Dict[str, Any]:
    """How many invitations the shop may still create (seller/quota/info, the call the Sample
    requests page makes; checked live 26 Sep 2026 on three shops):
    {can_invite, reason, reason_text, limit_24h, used_24h, active_limit}.

    limit_24h / used_24h: invitations created in the last 24 hours and the cap on them
    (200 on every shop checked). used_24h counted exactly the invitations created in the 24
    hours before the call, not since midnight. active_limit: most likely the cap on
    invitations open at once (effective_group_cnt_limit, 1000 on every shop checked)."""
    info = call(api, "/oec/affiliate/seller/quota/info", {}).get("invitation_rate_limit_info") or {}
    reason = _as_int(info.get("reason")) or 0
    can = bool(info.get("can_invite", True))
    return {"can_invite": can, "reason": reason,
            "reason_text": None if can else LIMIT_REASONS.get(reason, f"TikTok refuses new invitations (reason {reason})"),
            "limit_24h": _as_int(info.get("group_cnt_limit_for24")),
            "used_24h": _as_int(info.get("current_group_cnt_for24")),
            "active_limit": _as_int(info.get("effective_group_cnt_limit"))}


def invitation_limits(api: ShopApi) -> Dict[str, Optional[int]]:
    """What one invitation may hold (invitation_group/invitation/limit, the call the create
    page makes): {max_creators, max_products} -- 50 and 100 on every shop checked. The create
    page splits creators on max_creators and TikTok refuses more. Invitations made through
    other channels can already hold more (one had 479 creators, 26 Sep 2026)."""
    data = call(api, "/oec/affiliate/seller/invitation_group/invitation/limit", None, "GET")
    return {"max_creators": _as_int(data.get("max_creator_num")), "max_products": _as_int(data.get("max_product_num"))}


def _creator_refs(creator_ids: Iterable[str]) -> List[Dict[str, Any]]:
    return [{"base_info": {"creator_id": "", "nick_name": "", "creator_oec_id": str(c)}} for c in creator_ids]


def conflicting_creators(api: ShopApi, products: List[Dict[str, Any]], creator_ids: List[str]) -> Dict[str, str]:
    """{creator_id: invitation name} for creators already holding one of these products in
    another open invitation (the page runs the same check on Send)."""
    data = call(api, "/oec/affiliate/seller/invitation_group/conflict_check", {
        "invitation": {"id": "", "product_list": products, "creator_id_list": _creator_refs(creator_ids)}})
    where: Dict[str, str] = {}
    for inv in data.get("conflict_list") or []:
        for c in inv.get("creator_id_list") or []:
            cid = ((c or {}).get("base_info") or {}).get("creator_oec_id")
            if cid:
                where.setdefault(str(cid), inv.get("name") or str(inv.get("id")))
    out: Dict[str, str] = {}
    for group in data.get("conflict_cids") or []:
        for cid in group.get("cids") or []:
            out[str(cid)] = where.get(str(cid), "another invitation")
    return out


def detail(api: ShopApi, invitation_id: str) -> Dict[str, Any]:
    """One invitation with its creators. The body key is `invitation_group_id`, not the
    `invitation_id` the page's URL shows -- a trap."""
    return call(api, "/oec/affiliate/seller/invitation_group/detail",
                {"invitation_group_id": str(invitation_id)}).get("invitation") or {}


def creators_of(invitation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The creators in a detail answer, with what they did: products added to the showcase,
    content posted. Avatar URLs are signed and expire, so they are not returned."""
    out = []
    for c in invitation.get("creator_id_list") or []:
        base = (c or {}).get("base_info") or {}
        cid = base.get("creator_oec_id") or base.get("creator_id")
        if not cid:
            continue
        out.append({
            "id": str(cid),
            "status": _as_int(c.get("group_creator_status")) or 0,
            "handle": base.get("user_name"),
            "nickname": base.get("nick_name"),
            "added": _as_int(c.get("product_add_cnt")) or 0,
            "posted": _as_int(c.get("content_posted_cnt")) or 0,
            "effective": _as_int(c.get("effective_status")),
        })
    return out


def _price(p: Dict[str, Any], key: str) -> Optional[str]:
    return (((p.get(key) or {}).get("min_price") or {}).get("format_price"))


def info_of(invitation: Dict[str, Any]) -> Dict[str, Any]:
    """What the detail page shows above its creators, from a detail answer: settings and
    products (image URLs are signed; read, never keep)."""
    rule = invitation.get("free_sample_rule") or {}
    content = _as_int((invitation.get("delivery_requirements") or {}).get("content_option"))
    whatsapp = next((c.get("value") for c in invitation.get("contacts_info") or []
                     if c.get("field") == 6 and c.get("value")), None)
    products = []
    for p in invitation.get("product_list") or []:
        image = p.get("image") or {}
        products.append({
            "product_id": str(p.get("product_id") or ""),
            "title": p.get("title"),
            "image_url": (image.get("thumb_url_list") or image.get("url_list") or [None])[0],
            "price": _price(p, "price"),
            "promotion_price": _price(p, "promotion_price"),
            "target_commission": _as_int(p.get("target_commission")),
            "ads_commission": _as_int(p.get("target_ads_commission")),
            "open_commission": _as_int(p.get("open_commission")),
            "stock": _as_int(p.get("stock")),
            "item_sold": _as_int(p.get("item_sold")),
        })
    return {
        "invitation_id": str(invitation.get("id") or ""),
        "name": invitation.get("name"),
        "message": invitation.get("message"),
        "whatsapp": whatsapp,
        "start_time": _as_int(invitation.get("start_time")),
        "end_time": _as_int(invitation.get("end_time")),
        "group_status": _as_int(invitation.get("group_status")),
        "group_type": _as_int(invitation.get("group_type")),
        "content_type": {v: k for k, v in CONTENT_OPTIONS.items()}.get(content, "NO_PREFERENCE"),
        "free_sample": bool(rule.get("has_free_sample")),
        "free_sample_auto_review": bool(rule.get("is_free_sample_auto_review")),
        "products": products,
    }


# ============================================================================ as stored
#
# The shape GrowSeller keeps an invitation in, and sends back through group_body():
#   invitation {title, valid_until, phone, content_type, sample_type, message}
#   products   [{id, standard_commission, ads_commission}]

def invitation_of(invitation: Dict[str, Any]) -> Dict[str, Any]:
    """A detail answer's settings, the inverse of group_body() -- sending it back unchanged
    changes nothing on TikTok."""
    rule = invitation.get("free_sample_rule") or {}
    sample = ("AUTO" if rule.get("is_free_sample_auto_review") else "MANUAL") if rule.get("has_free_sample") else None
    end = _as_int(invitation.get("end_time"))
    content = _as_int((invitation.get("delivery_requirements") or {}).get("content_option"))
    return {
        "title": invitation.get("name") or "",
        "valid_until": (datetime.fromtimestamp(end / 1000, ZoneInfo(tt.TIMEZONE)).strftime("%Y-%m-%d")
                        if end else None),
        "phone": next((c.get("value") for c in invitation.get("contacts_info") or []
                       if c.get("field") == 6 and c.get("value")), None),
        "content_type": {v: k for k, v in CONTENT_OPTIONS.items()}.get(content, "NO_PREFERENCE"),
        "sample_type": sample,
        "message": invitation.get("message") or "",
    }


def products_of(invitation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A detail answer's products with the commissions it offers."""
    return [{"id": str(p["product_id"]),
             "standard_commission": _as_int(p.get("target_commission")) or 0,
             "ads_commission": _as_int(p.get("target_ads_commission"))}
            for p in invitation.get("product_list") or [] if p.get("product_id")]


def stored_group(invitation: Dict[str, Any], products: List[Dict[str, Any]],
                 creator_ids: Iterable[str], name: Optional[str] = None) -> Dict[str, Any]:
    """group_body() from the stored shape."""
    return group_body(name=name or invitation.get("title") or "", message=invitation.get("message") or "",
                      whatsapp=invitation.get("phone"), valid_until=invitation.get("valid_until"),
                      sample_type=invitation.get("sample_type"), content_type=invitation.get("content_type"),
                      products=[{"product_id": p["id"], "standard_commission": p.get("standard_commission") or 0,
                                 "ads_commission": p.get("ads_commission")} for p in products],
                      creator_ids=creator_ids)


def terminate(api: ShopApi, invitation_id: str, group_type: int = 1) -> None:
    """End it. TikTok has no delete: an ended invitation stays, as Canceling then Completed."""
    call(api, "/oec/affiliate/seller/invitation_group/terminate",
         {"invitation_group_id": str(invitation_id), "group_type": group_type})


# ============================================================================ create / update

def clean_phone(v: Any) -> str:
    """Contact phone as bare digits: a formatted one ('+62 821-2560-9740') is refused with
    98001004; the country code (ID#62) goes separately and a leading 62/0 is accepted."""
    return re.sub(r"\D", "", str(v or ""))


def product_list(products: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Products as create, update and conflict_check take them. Each product: product_id (or id),
    standard_commission, and optionally ads_commission (basis points, as the page sends)."""
    out = []
    for p in products or []:
        item = {"product_id": str(p.get("product_id") or p.get("id")),
                "target_commission": int(p.get("standard_commission", 0))}
        if p.get("ads_commission") is not None:
            item["target_ads_commission"] = int(p["ads_commission"])
        out.append(item)
    return out


def free_sample_rule(sample_type: Optional[str]) -> Dict[str, Any]:
    """Exactly what the create page sends: two booleans, no sample_setting_type (None of the
    invitations sellers made on TikTok itself carry it; read back 23 Sep 2026)."""
    return {"has_free_sample": bool(sample_type), "is_free_sample_auto_review": sample_type == "AUTO"}


def end_time_ms(valid_until: Optional[str]) -> str:
    """End of `valid_until` (YYYY-MM-DD) in the market's timezone, as epoch ms; 30 days from now
    when absent. A naive timestamp drifts a day on a UTC server (23:59 Jakarta became 06:59)."""
    try:
        if valid_until:
            d = datetime.strptime(str(valid_until)[:10], "%Y-%m-%d")
            return str(int(d.replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo(tt.TIMEZONE)).timestamp() * 1000))
    except (TypeError, ValueError):
        pass
    return str(int(time.time() * 1000) + 30 * 86400 * 1000)


def _contacts(whatsapp: Any) -> List[Dict[str, Any]]:
    """WhatsApp (field 6) when given, plus the two contact fields TikTok silently requires."""
    out = []
    phone = clean_phone(whatsapp)
    if phone:
        out.append({"title": "", "field": 6, "value": phone, "country_code": "ID#62"})
    out += [{"title": "", "field": 41, "value": "", "country_code": "ID#62"},
            {"title": "", "field": 45, "value": "", "country_code": "ID#62"}]
    return out


def group_body(name: str, message: str, whatsapp: Any, valid_until: Optional[str], sample_type: Optional[str],
               content_type: Optional[str], products: Iterable[Dict[str, Any]],
               creator_ids: Iterable[str]) -> Dict[str, Any]:
    """The invitation object the create page builds (update sends it with the id)."""
    return {
        "name": name,
        "message": message or "",
        "contacts_info": _contacts(whatsapp),
        "group_type": 1,
        "free_sample_rule": free_sample_rule(sample_type),
        "end_time": end_time_ms(valid_until),
        "product_list": product_list(products),
        "creator_id_list": _creator_refs(creator_ids),
        "delivery_requirements": {"content_option": CONTENT_OPTIONS.get(content_type or "NO_PREFERENCE", 1)},
    }


def create(api: ShopApi, group: Dict[str, Any]) -> Any:
    """Create an invitation from group_body(); TikTok's decoded answer (code 0 on success)."""
    q = api.common_query()
    return decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/oec/affiliate/seller/invitation_group/create",
                                  q, {"invitation_group": group}, api.headers(create_referer(q.get("oec_seller_id", "")))))


# free_sample_rule.sample_setting_type (0 none, 1 manual review, 2 auto-approve). An update
# without it keeps the invitation's current setting: AUTO -> MANUAL was answered code 0 and
# ignored until it was sent (live test on Jagoan Resi, 26 Sep 2026).
SAMPLE_SETTING_TYPES = {None: 0, "MANUAL": 1, "AUTO": 2}


def update(api: ShopApi, invitation_id: str, group: Dict[str, Any]) -> Any:
    """Replace an invitation's configuration and creators (the edit page's body: the create
    object plus the id, and has_flash_sale). The free-sample rule carries its
    sample_setting_type, or TikTok leaves the setting as it was."""
    q = api.common_query()
    rule = dict(group.get("free_sample_rule") or {})
    kind = ("AUTO" if rule.get("is_free_sample_auto_review") else "MANUAL") if rule.get("has_free_sample") else None
    rule["sample_setting_type"] = SAMPLE_SETTING_TYPES[kind]
    body = {"invitation": {"id": str(invitation_id), **group, "free_sample_rule": rule, "has_flash_sale": False}}
    return decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/oec/affiliate/seller/invitation_group/update",
                                  q, body, api.headers(create_referer(q.get("oec_seller_id", "")))))


def succeeded(response: Any) -> bool:
    return isinstance(response, dict) and response.get("code") == 0


def error_of(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("message") or response.get("msg") or response)
    return str(response)


# ============================================================================ product picker

PRODUCT_PAGE_SIZE = tt.PRODUCT_SELECTION_MAX_PAGE_SIZE


def product_selection(api: ShopApi, keyword: str = "", product_id: str = "", search_mode: str = "auto",
                      category_id: str = "", page: int = 1, page_size: int = 20) -> Any:
    """The create page's product search (product_selection/list), TikTok's decoded answer.
    search_mode: "id", "name", or "auto" (a keyword of 10+ digits is a product id).
    page_size at most PRODUCT_PAGE_SIZE."""
    page_size = min(page_size, PRODUCT_PAGE_SIZE)
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    params = []
    pid, kw = product_id.strip(), keyword.strip()
    if pid:
        params.append({"key": 2, "search_type": 2, "value": pid})
    elif kw:
        by_id = search_mode == "id" or (search_mode == "auto" and kw.isdigit() and len(kw) >= 10)
        params.append({"key": 2, "search_type": 2, "value": kw} if by_id else {"key": 1, "search_type": 1, "value": kw})
    if category_id:
        params.append({"key": 3, "search_type": 2, "value": category_id})
    body = {"page_size": page_size, "cur_page": page, "source": 2, "search_params": params}
    return decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/affiliate/product_selection/list", q, body,
                                  api.headers(create_referer(q.get("oec_seller_id", "")))))


def product_categories(api: ShopApi, level: int = 1, parent_category_id: str = "") -> Any:
    """Product categories one level at a time (product_category/list), TikTok's decoded answer."""
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    q["level"] = str(level)
    if parent_category_id:
        q["parent_category_id"] = parent_category_id
    return decode_body(api.signed("GET", f"{tt.AFFILIATE_API}/affiliate/product_category/list", q, None,
                                  api.headers(create_referer(q.get("oec_seller_id", "")))))


def clean_product(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A product_selection item reduced to what a picker shows."""
    def price_text(obj: Any) -> Tuple[Optional[str], Any, Any]:
        if isinstance(obj, dict):
            lo, hi = obj.get("min_price_format"), obj.get("max_price_format")
            text = f"{lo} - {hi}" if lo and hi and lo != hi else (lo or hi)
            return text, obj.get("min_price"), obj.get("max_price")
        if isinstance(obj, (str, int, float)):
            return str(obj), None, None
        return None, None, None

    price, min_p, max_p = price_text(raw.get("price") or {})
    if price is None and isinstance(raw.get("price"), dict):
        price = str(min_p or "")
    sale_price, _, _ = price_text(raw.get("sale_price") or {})
    cats = raw.get("category_info") or []
    first = cats[0] if isinstance(cats, list) and cats and isinstance(cats[0], dict) else {}
    stock = raw.get("stock")
    if stock is None and isinstance(raw.get("stock_info"), dict):
        stock = raw["stock_info"].get("spu_stock")
    return {
        "product_id": str(raw.get("product_id") or ""),
        "title": raw.get("title") or raw.get("product_name") or "",
        "image_url": raw.get("image_url") or raw.get("product_image") or "",
        "price": price,
        "sale_price": sale_price,
        "min_price": str(min_p) if min_p is not None else None,
        "max_price": str(max_p) if max_p is not None else None,
        "sales": str(raw.get("sales", 0)),
        "stock": str(stock if stock is not None else 0),
        "status": int(raw.get("status", 1)),
        "country": raw.get("country", tt.REGION),
        "category_id": str(first.get("category_id") or "") if first else None,
        "category_name": (first.get("category_name") or first.get("name")) if first else None,
        "create_time": str(raw.get("create_time") or ""),
    }


# ============================================================================ one creator, one level down
#
# The detail page's "View details" drawer and the Video/LIVE counts inside it (live page
# 24 Sep 2026). Detail only says how many products a creator added and posted; which ones,
# and with how many videos and LIVEs each, is here.

def creator_products(api: ShopApi, invitation_id: str, creator_id: str) -> List[Dict[str, Any]]:
    """What one creator did with each product of the invitation: [{product_id, in_showcase,
    video_count, live_count, commission_effective, product}], `product` being TikTok's
    product_base_info (title, prices, commissions, stock) for the caller's product master."""
    data = call(api, "/oec/affiliate/seller/invitation_group/creator_promotion_detail",
                {"creator_id": str(creator_id), "invitation_group_id": str(invitation_id)})
    out = []
    for p in data.get("product_detail_list") or []:
        base = (p or {}).get("product_base_info") or {}
        if not base.get("product_id"):
            continue
        out.append({
            "product_id": str(base["product_id"]),
            "in_showcase": bool(p.get("product_add_status")),
            # Absent, not zero, when the product was never added.
            "video_count": _as_int(p.get("video_count")) or 0,
            "live_count": _as_int(p.get("live_count")) or 0,
            "commission_effective": bool(p.get("commission_effective")),
            "product": base,
        })
    return out


def creator_videos(api: ShopApi, invitation_id: str, creator_id: str, product_id: str,
                   offset: int = 0, size: int = 20) -> Dict[str, Any]:
    """The videos one creator posted for one product of the invitation, newest first as TikTok
    orders them: {videos: [{item_id, name, release_date, play_cnt, like_cnt, comment_cnt,
    cover_url}], has_more}. Cover and play URLs are signed and expire, so read, never keep."""
    data = call(api, "/oec/affiliate/seller/invitation_group/creator_video_list", {
        "creator_id": str(creator_id), "invitation_group_id": str(invitation_id),
        "product_id": str(product_id), "size": size, "offset": offset})
    videos = []
    for v in data.get("video_list") or []:
        media = (v or {}).get("video") or {}
        videos.append({
            "item_id": str(v.get("item_id") or ""),
            "name": (v.get("name") or "").strip(),
            "release_date": _as_int(v.get("release_date")),
            "play_cnt": _as_int(v.get("play_cnt")) or 0,
            "like_cnt": _as_int(v.get("like_cnt")) or 0,
            "comment_cnt": _as_int(v.get("comment_cnt")) or 0,
            "cover_url": media.get("post_url"),
        })
    return {"videos": videos, "has_more": bool(data.get("has_more"))}
