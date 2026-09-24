"""Shop analytics: the four Detail tables of the Affiliate Center's "Performa" page.

All four tabs (creator / product / video / live) come from ONE TikTok endpoint,
compass/transaction/detail_list/get, distinguished by detail_list_type +
module_type + the params key. TABS below is that mapping; everything else here is
generic over it.

TikTok paginates, sorts and filters server-side, so a page view is one call for the rows on
screen. Video play URLs in the answer are signed and expire within days: fetch, don't store.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .. import constants as tt
from ..client import ok_data
from . import ShopApi

# Jakarta. TikTok reports this shop's data on GMT+7 day boundaries and the numbers
# shift if we ask on any other offset, so windows are built in WIB, not UTC.
TZ_OFFSET = 25200
_WIB = timezone(timedelta(seconds=TZ_OFFSET))

# TikTok's own preset codes; sending the matching one keeps totals identical to the
# TikTok page. Any other span is a custom range (1), which TikTok also accepts.
_TIME_OPTION = {7: 3, 30: 4}

MAX_PAGE_SIZE = 100  # 200 answers 98001001 "internal error"

# TikTok's own max_duration for the four Detail tabs is 31 days (2678400s). Only the
# fallback for when available_date/get is unreachable; the live number wins.
MAX_RANGE_DAYS = 31

# How far behind TikTok is, is not a constant and must never be guessed. Its own
# compass/available_date/get answers with the exact bounds per tab, and they disagree
# with each other: on 23 Sep 2026 creator and product ended at 20 Sep while video and
# live ended at 21 Sep, and the day before all four ended at 20 Sep. A window that
# reaches into a day TikTok has not computed comes back total=0, not as an error — the
# table just looks like a shop with no sales. So the bounds are asked for, and
# DATA_LAG_DAYS is only the floor to fall back on when that call fails.
DATA_LAG_DAYS = 3

# Today is reachable after all, but only through TikTok's own "Hari ini (real-time)"
# preset — a narrower report, not the same window with a later end date. It sends
# time_option_type 2 over today..tomorrow, drops the metrics it cannot compute yet,
# drops the collaboration filter, and asks for no comparison period. The key sets below
# are exactly what its page requests, per tab, verified 22 Sep 2026 against the live
# page. Produk has no real-time build yet ("Data real-time segera hadir") and fires no
# request at all, so it is absent here and stays on the lagged window.
REALTIME_TIME_OPTION = 2
REALTIME_METRICS: Dict[str, Tuple[str, ...]] = {
    "creator": (
        "creator_gmv", "live_gmv", "video_gmv", "creator_orders_cnt",
        "creator_items_sold_cnt", "creator_average_order_value", "product_card_gmv",
        "creator_open_collaboration_gmv", "creator_target_collaboration_gmv",
        "creator_product_ctr", "creator_impressions_cnt", "distinct_creator_buyers_cnt",
        "creator_distinct_products_with_sales_cnt", "creator_estimated_commission",
    ),
    "video": (
        "video_gmv", "video_orders_cnt", "video_average_order_value",
        "video_items_sold_cnt", "video_distinct_product_ctr", "video_distinct_gpm",
        "video_estimated_commission",
    ),
    "live": (
        "live_gmv", "live_items_sold_cnt", "live_orders_cnt", "live_average_order_value",
        "live_product_impressions_cnt", "live_product_clicks_cnt", "live_distinct_gpm",
        "live_average_gmv_per_buyer", "live_distinct_product_ctr",
        "live_estimated_commission",
    ),
}

# Keys that exist only in the real-time report, so the historical one must not ask
# for them — it would answer with columns of blanks that read as zeroes.
REALTIME_ONLY = frozenset({"creator_open_collaboration_gmv",
                           "creator_target_collaboration_gmv"})

# key -> TikTok metric_type code, in the order the real page requests them.
# The code is also the `sorter.sort_type` for that column, which is what makes
# clicking a column header sort server-side across the whole result, not just the
# 50 rows on screen. Mapping verified one code at a time against the live API.
METRICS: Dict[str, Dict[str, int]] = {
    "creator": {
        "creator_gmv": 2, "live_gmv": 26, "video_gmv": 27, "creator_refunded_gmv": 3,
        "creator_orders_cnt": 5, "creator_items_sold_cnt": 6, "creator_refunded_items_cnt": 7,
        "creator_average_order_value": 10, "product_card_gmv": 28,
        "creator_click_to_order_rate": 18, "creator_live_cnt": 9, "creator_video_cnt": 8,
        "creator_sample_content_cnt": 17, "creator_samples_shipped_cnt": 12,
        "distinct_showcase_product_cnt": 24, "creator_product_ctr": 14,
        "creator_impressions_cnt": 15, "creator_video_views_cnt": 19,
        "distinct_creator_buyers_cnt": 20, "creator_distinct_products_with_sales_cnt": 21,
        "creator_estimated_commission": 4,
        # Only the real-time report asks for these two; the historical one has no such
        # columns. Codes read off the live page's own request, names off its response.
        "creator_target_collaboration_gmv": 22, "creator_open_collaboration_gmv": 23,
    },
    "product": {
        "product_gmv": 1, "product_items_sold_cnt": 3, "product_refunded_gmv": 2,
        "product_refunded_items_cnt": 4, "product_orders_cnt": 9,
        "product_click_to_order_rate": 20, "product_video_cnt": 13, "product_live_cnt": 14,
        "product_sample_content_cnt": 19, "product_samples_shipped_cnt": 6,
        "product_distinct_videos_with_sales_cnt": 24,
        "product_distinct_lives_with_sales_cnt": 22, "product_ctr": 16,
        "product_impressions_cnt": 17, "product_clicks_cnt": 18,
        "product_distinct_promoting_creators_cnt": 25,
        "product_distinct_sales_creators_cnt": 26, "product_distinct_buyers_cnt": 23,
        "product_estimated_commission": 5,
    },
    "video": {
        "video_gmv": 1, "video_orders_cnt": 2, "video_average_order_value": 5,
        "video_items_sold_cnt": 14, "video_refunded_gmv": 15, "video_refunded_items_cnt": 16,
        "video_likes_cnt": 7, "video_comments_cnt": 8, "video_shares_cnt": 9,
        "video_product_impressions_cnt": 18, "video_product_clicks_cnt": 19,
        "video_finish_rate": 17, "video_views_cnt": 3, "video_distinct_product_ctr": 21,
        "video_distinct_gpm": 22, "video_distinct_engagement_rate": 20,
        "video_average_gmv_per_buyer": 11, "video_estimated_commission": 12,
    },
    "live": {
        "live_gmv": 1, "live_items_sold_cnt": 2, "live_refunded_gmv": 3,
        "live_orders_cnt": 8, "live_average_order_value": 19,
        "live_average_live_viewing_duration": 6, "live_likes_cnt": 11,
        "live_comments_cnt": 12, "live_shares_cnt": 13,
        "live_product_impressions_cnt": 15, "live_product_clicks_cnt": 17,
        "live_impressions_cnt": 20, "live_distinct_gpm": 27,
        "live_distinct_products_cnt": 25, "live_average_gmv_per_buyer": 9,
        "live_distinct_engagement_rate": 24, "live_distinct_product_ctr": 26,
        "live_distinct_unique_live_viewers_cnt": 22, "live_distinct_enter_room_rate": 23,
        "live_estimated_commission": 21,
    },
}

TABS: Dict[str, Dict[str, Any]] = {
    "creator": {
        "detail_list_type": 1,
        "module_type": 103,
        "params_key": "creator_list_params",
        "export_key": "transaction_creator_list_param",
        "segments_key": "creator_list_segments",
        "perf_key": "creator_performances",
        "metrics_key": "creator_metrics",
        "search_key": "creator_handle_name",
        "default_sort": "creator_gmv",
        # Only this tab gets a comparison period back. TikTok's page asks for one here
        # and nowhere else, which is why its UI shows +/-% on Kreator only.
        "compare": True,
    },
    "product": {
        "detail_list_type": 2,
        "module_type": 104,
        "params_key": "product_list_params",
        "export_key": "transaction_product_list_param",
        "segments_key": "product_list_segments",
        "perf_key": "product_performances",
        "metrics_key": "product_metrics",
        "search_key": "product_name",
        "default_sort": "product_gmv",
    },
    "video": {
        "detail_list_type": 3,
        "module_type": 107,
        "params_key": "video_list_params_v2",
        "export_key": "transaction_video_list_params_v2",
        "segments_key": "video_list_segments_v2",
        "perf_key": "video_performances",
        "metrics_key": "video_metrics",
        "search_key": "video_name",
        "date_filter_key": "video_post_date",
        "default_sort": "video_gmv",
    },
    "live": {
        "detail_list_type": 4,
        "module_type": 108,
        "params_key": "live_list_params_v2",
        "export_key": "transaction_live_list_params_v2",
        "segments_key": "live_list_segments_v2",
        "perf_key": "live_performances",
        "metrics_key": "live_metrics",
        "search_key": "live_name",
        "date_filter_key": "live_start_date",
        "default_sort": "live_gmv",
    },
}

MODULES = tuple(TABS)


# ------------------------------------------------------------------ plumbing

def referer(api: ShopApi) -> str:
    return f"{tt.AFFILIATE}/insights/transaction-analysis?shop_region={api.region}&shop_id={api.seller_id or ''}"


def call(api: ShopApi, path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST",
         extra_query: Optional[Dict[str, str]] = None) -> bytes:
    """One compass call as the Performa page makes it; the raw answer."""
    q = api.common_query()
    q["user_language"] = tt.LOCALE
    if extra_query:
        q.update(extra_query)
    return api.signed(method, f"{tt.AFFILIATE_API}{path}", q, body, api.headers(referer(api)))


def json_call(api: ShopApi, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return ok_data(call(api, path, body), str(path.rsplit("/", 2)[-2:])).get("data") or {}


_ONE_DAY = timedelta(days=1)


def today_wib() -> date:
    """The current WIB day. Never date.today(): the server clock may be on any offset,
    and near WIB midnight the two disagree about which day it is."""
    return datetime.now(_WIB).date()


def latest_reported_day() -> date:
    """Fallback only, for when TikTok will not tell us. Prefer available_range()."""
    return today_wib() - timedelta(days=DATA_LAG_DAYS)


def available_range(api: ShopApi, module: str) -> Dict[str, Any]:
    """TikTok's own bounds for one tab: {start, end, max_days}, all WIB dates inclusive.

    This is what its date picker greys dates out by, and it moves on TikTok's schedule, not
    the calendar's. `end_time` comes back as the midnight AFTER the last usable day, the same
    convention the list call wants, so the last usable day is one day back. Raises when TikTok
    does not answer; callers fall back to latest_reported_day().
    """
    code = TABS[module]["module_type"]
    data = json_call(api, "/oec/affiliate/compass/available_date/get", {"module_types": [code]})
    row = next(r for r in (data.get("available_time_range_list") or []) if r.get("module_type") == code)
    return {"start": _day_of(row["start_time"]), "end": _day_of(row["end_time"]) - _ONE_DAY,
            "max_days": max(1, int(row["max_duration"]) // 86400)}


def _day_of(ts: int) -> date:
    """The WIB calendar day a TikTok timestamp lands on."""
    return datetime.fromtimestamp(ts, _WIB).date()


def window(days: int = 30, end_day: Optional[date] = None) -> Tuple[int, int, date, date]:
    """(start_ts, end_ts, start_date, end_date) for the last `days` reported WIB days.

    The window ends DATA_LAG_DAYS back, not today: TikTok has not computed the most
    recent two days and answers with an empty list rather than partial numbers.
    Both dates are inclusive, so `days` of them are covered.
    """
    end_d = end_day or latest_reported_day()
    start_d = end_d - timedelta(days=days - 1)
    return _ts(start_d), _end_ts(end_d), start_d, end_d


def today_window() -> Tuple[int, int, date, date]:
    """Today in WIB — the window TikTok's own real-time preset sends."""
    d = today_wib()
    return _ts(d), _end_ts(d), d, d


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_WIB).timestamp())


def _end_ts(d: date) -> int:
    """end_time is the midnight AFTER the last reported day, which is how TikTok's own
    page asks: its "7 hari terakhir" of 14–20 Sep sends 14 Sep 00:00 → 21 Sep 00:00.
    Sending midnight of the last day itself silently drops that day from the totals."""
    return _ts(d + _ONE_DAY)


def range_from_dates(start_d: date, end_d: date) -> Tuple[int, int, int]:
    """(start_ts, end_ts, days) for an explicit range the user picked, ends inclusive."""
    return _ts(start_d), _end_ts(end_d), (end_d - start_d).days + 1


def realtime_supported(module: str) -> bool:
    return module in REALTIME_METRICS


def metric_keys(module: str, realtime: bool = False) -> Tuple[str, ...]:
    """The metric keys this tab asks TikTok for, which is not every key it knows:
    the real-time report is a strict subset, and its two collaboration-GMV columns
    exist nowhere else."""
    if realtime:
        return REALTIME_METRICS[module]
    return tuple(k for k in METRICS[module] if k not in REALTIME_ONLY)


def _time_descriptor(start_ts: int, end_ts: int, days: int, with_context: bool = True,
                     option: Optional[int] = None) -> Dict[str, Any]:
    td = {
        "granularity_type": 1,
        "timezone_offset": TZ_OFFSET,
        "start_time": start_ts,
        "end_time": end_ts,
        "time_option_type": option or _TIME_OPTION.get(days, 1),
    }
    if with_context:
        td["time_context"] = "101"
    return td


# TikTok's own dropdown, in its order. A value outside 1-3 is rejected with 98001004.
COLLABORATION_TYPES = {1: "Kolaborasi terbuka", 2: "Kolaborasi bertarget", 3: "Mitra"}

# Traffic and interaction metrics are only collected for the unfiltered view; TikTok's own
# table drops these columns as soon as a collaboration type is picked, so we say which ones
# rather than showing numbers that quietly mean something else.
TRAFFIC_METRICS = {
    "creator": ("creator_click_to_order_rate", "creator_video_views_cnt",
                "creator_product_ctr", "creator_impressions_cnt"),
    "product": ("product_click_to_order_rate", "product_ctr", "product_impressions_cnt",
                "product_clicks_cnt"),
    "video": (), "live": (),
}


def _list_params(module: str, start_ts: int, end_ts: int, days: int, sort: str, order: str,
                 search: str, page_no: Optional[int], page_size: Optional[int],
                 collaboration_type: Optional[int] = None,
                 post_start: Optional[date] = None,
                 post_end: Optional[date] = None,
                 realtime: bool = False) -> Dict[str, Any]:
    """The per-tab params block shared by the list call and the export call."""
    spec = TABS[module]
    all_metrics = METRICS[module]
    metrics = {k: all_metrics[k] for k in metric_keys(module, realtime)}
    sort_key = sort if sort in metrics else spec["default_sort"]
    filt: Dict[str, Any] = {}
    if search.strip():
        filt[spec["search_key"]] = search.strip()
    # The real-time report is not collected per collaboration type; TikTok's page greys
    # the filter out rather than answering with a silently unfiltered table.
    if collaboration_type in COLLABORATION_TYPES and not realtime:
        filt["collaboration_type"] = collaboration_type
    date_key = spec.get("date_filter_key")
    if date_key and post_start and post_end:
        # Posting date is NOT the reporting period: a video posted last year still earns
        # GMV inside this month's window, so TikTok filters the two independently.
        filt[date_key] = {"start_time": _ts(post_start), "end_time": _ts(post_end),
                          "timezone_offset": TZ_OFFSET}
    params: Dict[str, Any] = {
        "time_descriptor": _time_descriptor(
            start_ts, end_ts, days, with_context=page_no is not None,
            option=REALTIME_TIME_OPTION if realtime else None),
        "metric_types": list(metrics.values()),
        # sort_type is the metric's own code; order_type 1 is descending.
        "sorter": {"sort_type": metrics[sort_key], "order_type": 1 if order != "asc" else 2},
        "filter": filt,
    }
    if page_no is not None:
        params["page_param"] = {"page_no": page_no, "page_size": min(page_size or 50, MAX_PAGE_SIZE)}
    # No comparison period in real-time: there is no equivalent slice of today to
    # compare against, and TikTok's page asks for none.
    if spec.get("compare") and not realtime:
        span = end_ts - start_ts
        params["previous_period_time_descriptor"] = {
            "start_time": start_ts - span,
            "end_time": start_ts,
            "timezone_offset": TZ_OFFSET,
            "granularity_type": 1,
            "time_option_type": params["time_descriptor"]["time_option_type"],
        }
    return params


# ------------------------------------------------------------------ list

def list_body(module: str, start_ts: int, end_ts: int, days: int, page: int = 1, page_size: int = 50,
              sort: str = "", order: str = "desc", search: str = "", collaboration_type: Optional[int] = None,
              post_start: Optional[date] = None, post_end: Optional[date] = None,
              realtime: bool = False) -> Dict[str, Any]:
    """The detail_list/get body for one page of one tab."""
    if module not in TABS:
        raise ValueError(f"unknown module {module!r}")
    if realtime and not realtime_supported(module):
        raise ValueError(f"{module} has no real-time report")
    spec = TABS[module]
    params = _list_params(module, start_ts, end_ts, days, sort, order, search, page, page_size,
                          collaboration_type, post_start, post_end, realtime)
    return {"params": {"detail_list_type": spec["detail_list_type"], spec["params_key"]: [params]},
            "module_type": spec["module_type"]}


def fetch_list(api: ShopApi, module: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """One page of one tab for a list_body(): {rows, total}, rows trimmed to what a table draws."""
    spec = TABS[module]
    data = json_call(api, "/oec/affiliate/compass/transaction/detail_list/get", body)
    segments = data.get(spec["segments_key"]) or []
    seg = segments[0] if segments else {}
    return {"rows": [_shape(module, r) for r in (seg.get(spec["perf_key"]) or [])],
            "total": int(seg.get("total") or 0)}


def _first_thumb(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        urls = obj.get("thumb_url_list")
        if isinstance(urls, list) and urls:
            return urls[0]
    return None


def _shape(module: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a TikTok row to what the table draws.

    Each row ships three signed variants of every avatar and cover plus a play_info
    holding several multi-KB CDN URLs. One of each is enough, and cutting the rest
    keeps a 50-row page a few hundred KB instead of megabytes.
    """
    spec = TABS[module]
    out: Dict[str, Any] = {
        "metrics": row.get(spec["metrics_key"]) or {},
        "prev_metrics": row.get("previous_period_" + spec["metrics_key"]) or {},
    }

    creator = row.get("creator_base") or {}
    if creator:
        out["creator"] = {
            "oec_id": creator.get("oec_id"),
            "handle_name": creator.get("handle_name"),
            "nick_name": creator.get("nick_name"),
            "avatar": _first_thumb(creator.get("avatar")),
            "follower_cnt": creator.get("follower_cnt"),
        }

    if module == "creator":
        out["id"] = creator.get("oec_id")
        out["label"] = creator.get("handle_name") or creator.get("nick_name") or ""
        cats = ((row.get("creator_extra") or {}).get("product_categories_detail") or {}).get("value")
        out["categories"] = [c.get("category_name") for c in (cats or []) if isinstance(c, dict)]
    elif module == "product":
        base = row.get("product_base") or {}
        out["id"] = base.get("id")
        out["label"] = base.get("title") or ""
        out["cover"] = _first_thumb(base.get("cover"))
        out["categories"] = [c.get("category_name") for c in (row.get("categories") or [])
                             if isinstance(c, dict)]
    elif module == "video":
        info = row.get("video_info") or {}
        out["id"] = info.get("item_id")
        out["label"] = info.get("title") or ""
        out["cover"] = _first_thumb(info.get("cover"))
        out["create_time"] = info.get("create_time")
        # One play URL, fetched fresh on every view. It is signed and short-lived,
        # which is exactly why nothing here is stored.
        urls = ((info.get("play_info") or {}).get("play_urls") or [])
        out["play_url"] = urls[0] if urls else None
        out["products"] = _products(row)
    else:
        info = row.get("live_info") or {}
        out["id"] = info.get("room_id")
        out["label"] = info.get("title") or ""
        out["cover"] = _first_thumb(info.get("cover"))
        out["start_time"] = (info.get("duration") or {}).get("start_time")
        out["end_time"] = (info.get("duration") or {}).get("end_time")
        out["products"] = _products(row)

    if not out.get("label"):
        # Untitled video / LIVE is common; the creator handle is what a seller looks for.
        out["label"] = creator.get("handle_name") or ""
    return out


def _products(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"id": p.get("id"), "title": p.get("title"), "cover": _first_thumb(p.get("cover"))}
            for p in (row.get("product_base_list") or []) if isinstance(p, dict)]


# ------------------------------------------------------------------ export

def export_create(api: ShopApi, module: str, start_ts: int, end_ts: int, days: int,
                  sort: str = "", order: str = "desc", search: str = "",
                  collaboration_type: Optional[int] = None,
                  post_start: Optional[date] = None,
                  post_end: Optional[date] = None,
                  realtime: bool = False) -> Dict[str, Any]:
    """Ask TikTok to build the XLSX. Returns whatever it says about the new task.

    The export params are the list params minus paging — TikTok exports the whole
    result (up to 50,000 rows per file, then it splits), so a caller never pages
    through thousands of rows to build a file TikTok already produces.
    """
    spec = TABS[module]
    params = _list_params(module, start_ts, end_ts, days, sort, order, search,
                          page_no=None, page_size=None,
                          collaboration_type=collaboration_type,
                          post_start=post_start, post_end=post_end, realtime=realtime)
    params.pop("previous_period_time_descriptor", None)
    body = {"module_type": spec["module_type"], spec["export_key"]: params}
    return json_call(api, "/oec/affiliate/compass/export_task/create", body)


def export_list(api: ShopApi, module: str) -> Dict[str, Any]:
    """Tasks for this tab, newest first as TikTok returns them. status 2 = ready."""
    body = {"module_type": TABS[module]["module_type"]}
    return json_call(api, "/oec/affiliate/compass/export_task/list", body)


def export_file(api: ShopApi, task_id: str, module: str) -> bytes:
    """The finished XLSX itself. This endpoint is a GET and answers with file bytes."""
    return call(
        api,
        "/oec/affiliate/compass/export_task/export",
        method="GET",
        extra_query={"task_id": task_id, "module_type": str(TABS[module]["module_type"])},
    )
