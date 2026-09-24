"""Creators as the Affiliate Center knows them: resolving handles (crm/creator/import_check) and
the marketplace profile (creator/marketplace/profile), with the profile decoded into flat fields.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from .. import constants as tt
from ..client import decode_body
from . import ShopApi

logger = logging.getLogger("tiktok_shop.affiliate.creators")

# Answers that mean TikTok is refusing (bot challenge / rate limit), not that the creator is bad.
REFUSAL_CODES = {10000, 10005, 40001, 98001004, 429}
PROFILE_TYPES = (1, 2, 3)  # PT1 basic/identity, PT2 performance, PT3 follower demographics


class Refused(RuntimeError):
    """TikTok answered with an anti-bot challenge or rate limit (REFUSAL_CODES)."""

    def __init__(self, code: Any, message: str):
        super().__init__(f"TikTok anti-bot challenge (code {code}): {message}")
        self.code = code
        self.message = message


def resolve_handles(api: ShopApi, handles: List[str]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Handles -> creators through import_check (the creator-management page's import).
    (valid [{creator_oecuid, handle, nickname}], unresolved handles). Raises Refused, or
    RuntimeError on any other failure."""
    if not handles:
        return [], []
    q = api.common_query()
    referer = (f"{tt.AFFILIATE}/affiliate/assets/creator-management"
               f"?shop_region={q.get('shop_region', tt.REGION)}&shop_id={q.get('oec_seller_id', '')}")
    res = decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/oec/affiliate/crm/creator/import_check", q,
                                 {"handle_names": handles}, api.headers(referer)))
    if not isinstance(res, dict) or res.get("code") != 0:
        code = res.get("code") if isinstance(res, dict) else None
        msg = res.get("message") if isinstance(res, dict) else str(res)[:200]
        if code in REFUSAL_CODES:
            raise Refused(code, str(msg))
        raise RuntimeError(f"TikTok import_check failed: {msg}")
    valid: List[Dict[str, str]] = []
    resolved = set()
    for c in (res.get("data", {}) or {}).get("creators", []) or []:
        base = c.get("base", {}) or {}
        oec_id = str(base.get("oec_id") or "")
        handle = base.get("handle_name") or ""
        if oec_id and oec_id != "0" and handle:
            valid.append({"creator_oecuid": oec_id, "handle": handle, "nickname": base.get("nick_name") or handle})
            resolved.add(handle.lower())
    return valid, [h for h in handles if h.lower() not in resolved]


def profile(api: ShopApi, oec_id: str, profile_type: int, query: Optional[Dict[str, str]] = None,
            headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """One profile type of a creator's marketplace profile (TikTok evaluates one per request), or
    None when TikTok answered something else. Raises Refused."""
    res = decode_body(api.signed("POST", f"{tt.AFFILIATE_API}/oec/affiliate/creator/marketplace/profile",
                                 query or api.common_query(),
                                 {"creator_oec_id": oec_id, "profile_types": [profile_type]},
                                 headers or api.headers()))
    if isinstance(res, dict) and res.get("code") == 0:
        cp = res.get("creator_profile")
        return cp if isinstance(cp, dict) else None
    code = res.get("code") if isinstance(res, dict) else None
    msg = res.get("message") if isinstance(res, dict) else res
    logger.warning("profile %s pt=%s non-zero code %s: %s", oec_id, profile_type, code, msg)
    if code in REFUSAL_CODES:
        raise Refused(code, str(msg))
    return None


# ============================================================================ decoding

def _n(v) -> float:
    """Parse a TikTok numeric (string/int/None or {value:...} wrapper) -> float, 0 if blank."""
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _pv(field):
    """Unwrap a creator_profile field's `.value` (fields are {value, is_authorized, status})."""
    return field.get("value") if isinstance(field, dict) else None


def _dist(lst, with_name: bool = False) -> List[Dict[str, Any]]:
    """Normalize a TikTok distribution list [{key,value,name?}] -> [{key, value:float(, name)}].
    Accepts raw list or wrapped {value: [...]} dict."""
    if isinstance(lst, dict):
        lst = lst.get("value")
    out: List[Dict[str, Any]] = []
    for x in (lst or []):
        if not isinstance(x, dict):
            continue
        item: Dict[str, Any] = {"key": x.get("key"), "value": _n(x.get("value"))}
        if with_name and x.get("name"):
            item["name"] = x["name"]
        out.append(item)
    return out


def profile_fields(profiles: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Map {profile_type: creator_profile} -> a flat dict of `creators` columns. Each field is read
    from the PT that owns it (PT1 identity, PT2 performance, PT3 demographics) so nothing is clobbered.
    Rate fields arrive x100 -> /100 as percent; gmv-like fields are nested {value:'...'}. Only keys we
    actually resolved are returned, so a partial fetch never overwrites good data with zeros."""
    p1, p2, p3 = profiles.get(1, {}), profiles.get(2, {}), profiles.get(3, {})
    g1, g2, g3 = p1.get, p2.get, p3.get
    f: Dict[str, Any] = {}

    if p1:  # identity / basic
        _handle = _pv(g1("handle"))  # PT1 handle is a wrapped {value,...} field, not a plain string
        if _handle:
            f["handle"] = _handle
        f["nickname"] = _pv(g1("nickname"))
        f["region"] = _pv(g1("selection_region"))
        f["follower_count"] = int(_n(g1("follower_cnt")))
        lvl = _pv(g1("creator_level"))
        if lvl is None or int(_n(lvl)) == 0:
            lvl_fb = _pv(g3("creator_level"))
            if lvl_fb is not None and int(_n(lvl_fb)) > 0:
                lvl = lvl_fb
        if lvl is not None:
            f["creator_level"] = int(_n(lvl))
        cia = _pv(g1("contact_info_available"))
        if cia is not None:
            f["contact_info_available"] = bool(cia)
        partner_val = _pv(g1("bounded_partner_name_offline")) or _pv(g1("creator_bind_mcn_name"))
        if partner_val and str(partner_val).strip():
            f["bounded_partner_name_offline"] = str(partner_val).strip()
        names, ids = [], []
        cat_val = g1("category")
        cat_list = cat_val.get("value") if isinstance(cat_val, dict) else (cat_val if isinstance(cat_val, list) else [])
        for c in cat_list:
            if not isinstance(c, dict):
                continue
            if c.get("name"):
                names.append(c["name"])
            sk = c.get("starling_key") or ""
            if sk:
                ids.append(sk.split("magellan_")[-1])
        if names:
            f["categories"], f["category_ids"] = names, ids

    if p2:  # performance
        rate_val = _pv(g2("sample_fulfillment_rate"))
        if rate_val is None:
            rate_val = _pv(g3("sample_fulfillment_rate")) or _pv(g1("sample_fulfillment_rate"))
        fulfillment_rate = _n(rate_val) / 100 if rate_val is not None else 0.0

        med_gmv = _n(_pv(g2("med_gmv_revenue")))
        cg = _dist(g2("content_groups")) if p2 else []
        showcase_ratio = 0.0
        for item in cg:
            if item.get("key") == "showcase_gmv":
                showcase_ratio = float(item.get("value") or 0.0)
        showcase_gmv = round(med_gmv * showcase_ratio, 2) if showcase_ratio > 0 else 0.0

        f.update({
            "med_gmv_revenue": med_gmv,
            "units_sold": int(_n(g2("units_sold"))),
            "avg_revenue_per_buyer": _n(_pv(g2("avg_revenue_per_buyer"))),
            "med_commission_rate": _n(g2("med_commission_rate")) / 100,
            "sample_fulfillment_rate": fulfillment_rate,
            "per_showcase_gmv": showcase_gmv,
            "promoted_product_num": int(_n(g2("promoted_product_num"))),
            "collaborated_brands_num": int(_n(g2("collaborated_brands_num"))),
            "gpm": _n(_pv(g2("gpm"))),
            "video_publish_cnt_30d": int(_n(g2("video_publish_cnt_30d"))),
            "video_med_view_cnt": int(_n(g2("video_med_view_cnt"))),
            "video_engagement": _n(g2("video_engagement")) / 100,
            "video_gmv": _n(_pv(g2("video_gmv"))),
            "live_streaming_cnt_30d": int(_n(g2("live_streaming_cnt_30d"))),
            "live_med_view_cnt": int(_n(g2("live_med_view_cnt"))),
            "live_engagement": _n(g2("live_engagement")) / 100,
            "live_gmv": _n(_pv(g2("live_gmv"))),
        })
        price_range = _pv(g2("product_price_range"))
        if price_range and str(price_range).strip():
            f["product_price_range"] = str(price_range).strip()
        end_ts = _pv(g2("sales_performance_end_time"))
        if end_ts is not None:
            try:
                ts_val = int(float(str(end_ts).strip()))
                if ts_val > 0:
                    f["sales_performance_end_time"] = datetime.datetime.fromtimestamp(ts_val, datetime.timezone.utc)
            except (ValueError, TypeError):
                pass

    # Distribution fields are wrapped {value:[...], is_authorized, status} — unwrap with _pv BEFORE
    # _dist (passing the raw wrapper makes _dist iterate its keys and return []).
    genders = _dist(g3("follower_genders_v2")) if p3 else []
    tot_gender = sum(gd.get("value", 0.0) for gd in genders)
    for gd in genders:
        val = gd.get("value", 0.0)
        pct = round(val / tot_gender * 100, 2) if tot_gender > 0 else round(val * 100, 2)
        if gd["key"] == "male":
            f["follower_male_pct"] = pct
        elif gd["key"] == "female":
            f["follower_female_pct"] = pct

    # top_follower_ages mapping (1: 18-24, 2: 25-34/25-35, 3: 35-44/36-44, 4: 45-54, 5: 55+)
    ages = _dist(g3("follower_ages_v2")) if p3 else []
    if ages:
        age_map = {"18-24": 1, "25-34": 2, "25-35": 2, "35-44": 3, "36-44": 3, "45-54": 4, "55+": 5}
        sorted_ages = sorted(ages, key=lambda x: x.get("value", 0.0), reverse=True)
        top_codes = []
        for a in sorted_ages:
            code = age_map.get(str(a.get("key", "")).strip())
            if code and code not in top_codes:
                top_codes.append(code)
            if len(top_codes) >= 2:
                break
        if top_codes:
            f["top_follower_ages"] = top_codes

    # breakdowns jsonb: display-only distributions (donuts / top locations). Include only the ones
    # that resolved to a non-empty list.
    channel = _dist(g2("content_groups")) if p2 else []
    category = _dist(g2("industry_groups"), with_name=True) if p2 else []
    locations = _dist(g3("follower_state_location"), with_name=True) if p3 else []
    bd: Dict[str, Any] = {}
    if channel:
        bd["gmv_by_channel"] = channel
    if category:
        bd["gmv_by_category"] = category
    if ages:
        bd["follower_ages"] = ages
    if genders:
        bd["follower_genders"] = genders
    if locations:
        bd["follower_locations"] = locations
    if bd:
        f["breakdowns"] = bd

    return f
