"""TikTok Shop chat REST, on the IM host (IM_API) as the seller/im page calls it.

/v1/* calls carry protobuf request envelopes (tiktok_shop.im.protobuf); /api/* calls are JSON. Both
go unsigned (ShopApi.send): the page signs its /api/* calls, but ours signed with X-Bogus, X-Gnarly
and a BSID are refused with code 10000 plus a captcha, while the same call unsigned answers code 0
(conversation/create and search_conversation_by_users, verified live 24 Sep 2026). The answers are JSON (the server picks JSON unless asked for protobuf), trimmed
here to what a chat UI shows. Every call needs the shop's IM token (affiliate.chat.im_token).
"""
import base64
import json
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional

from .. import constants as tt
from ..affiliate import ShopApi
from ..client import decode_body
from .protobuf import (_ld, _s, _vf, encode_request, encode_send_image_message, encode_send_message,
                       encode_send_product_message)

logger = logging.getLogger("tiktok_shop.im.rest")


def protobuf_headers(token: str) -> Dict[str, str]:
    """Headers of a /v1/* protobuf call. No `accept: application/x-protobuf`: with the default
    JSON accept the IM server answers JSON, which is what this module parses."""
    return {"x-im-paas-token": token, "content-type": "application/x-protobuf",
            "origin": tt.AFFILIATE, "referer": f"{tt.AFFILIATE}/"}


def json_headers(token: str) -> Dict[str, str]:
    return {"x-im-paas-token": token, "content-type": "application/json",
            "origin": tt.AFFILIATE, "referer": f"{tt.AFFILIATE}/"}


def conversation_list(api: ShopApi, token_data: Dict[str, Any], cursor: int = 0, limit: int = 20,
                      conv_type: int = 0, query: Optional[str] = None) -> Dict[str, Any]:
    """Fetch conversation list via Protobuf wire (cmd 690) with folder filtering and optional search query."""
    token = token_data.get("token", "")
    user_info = token_data.get("user", {})
    uid = str(user_info.get("user_id", ""))

    group_map = {0: "s_all", 1: "s_unread", 2: "s_un_reply", 3: "s_archived", 4: "s_star"}
    group = group_map.get(conv_type, "s_all")

    inner = bytearray()
    inner += _ld(1, _vf(1, int(uid) if uid.isdigit() else 0) + _s(2, group))
    inner += _vf(2, cursor)
    inner += _vf(3, 0)
    inner += _vf(4, limit)

    req_body = _ld(690, _ld(3, bytes(inner)))
    req = encode_request(690, req_body, token, uid)

    url = f"{tt.IM_API}/v1/conversation/get_group_list"
    headers = protobuf_headers(token)

    raw = api.send("POST", url, req, headers)

    try:
        top = json.loads(raw.decode())
        if top.get("status_code") != 0:
            return {"conversations": [], "has_more": False, "next_cursor": 0, "total_count": 0, "error": top.get("error_desc")}

        body = top.get("body", {}).get("get_conversation_group_list_body", {})
        data = body.get("data", [])

        conversations = []
        next_cursor = cursor
        has_more = False
        total = 0

        if data:
            group_data = data[0]
            next_cursor = group_data.get("cursor", cursor)
            has_more = group_data.get("has_more", False)
            total = group_data.get("total_count", 0)

            for conv in group_data.get("conversations", []):
                ci = conv.get("conversation_info", {})
                msgs = conv.get("messages", [])

                creator_oec_id = None
                handle = None
                avatar = None

                biz_ext_raw = ci.get("biz_ext")
                if biz_ext_raw:
                    try:
                        b_decoded = json.loads(base64.b64decode(biz_ext_raw).decode("utf-8"))
                        creator_oec_id = b_decoded.get("creator_oec_id")
                        handle = b_decoded.get("handle")
                        avatar = b_decoded.get("avatar")
                    except Exception:
                        pass

                core_info = ci.get("conversation_core_info", {})
                core_ext = core_info.get("ext", {})
                if not creator_oec_id:
                    creator_oec_id = core_ext.get("creator_oec_id")

                creator_name = handle or core_info.get("name") or ""

                # Optional search query filter
                if query:
                    q_lower = query.lower()
                    name_match = (creator_name and q_lower in creator_name.lower()) or (handle and q_lower in handle.lower())
                    oec_match = bool(creator_oec_id and q_lower in str(creator_oec_id).lower())
                    cid_match = q_lower in str(ci.get("conversation_short_id", ""))
                    msg_text = msgs[0].get("content", "") if msgs else ""
                    msg_match = bool(msg_text and q_lower in str(msg_text).lower())
                    if not (name_match or oec_match or cid_match or msg_match):
                        continue

                participants = []
                seller_uids = {str(uid)} if uid else set()
                first_page = ci.get("first_page_participants", {})
                for p in first_page.get("participants", []):
                    p_uid = str(p.get("user_id", ""))
                    is_p_seller = p.get("role") == 1
                    if is_p_seller and p_uid:
                        seller_uids.add(p_uid)
                    role = "seller" if is_p_seller else "creator"
                    participants.append({
                        "user_id": p_uid,
                        "role": role,
                        "ext": p.get("ext", {}),
                    })

                last_msg = None
                if msgs:
                    m = msgs[0]
                    sender = m.get("sender", "")
                    sender_id = str(sender) if isinstance(sender, int) else str(sender.get("uid", "")) if isinstance(sender, dict) else ""
                    content_str = m.get("content", "")
                    m_ext = m.get("ext") or {}
                    msg_type = m_ext.get("type") or str(m.get("message_type", "text"))

                    last_msg = {
                        "message_id": str(m.get("server_message_id", "")),
                        "content": content_str,
                        "message_type": msg_type,
                        "create_time": m.get("create_time", 0),
                        "index_in_conversation": m.get("index_in_conversation", 0),
                        "sender_id": sender_id,
                        "sender_role": "seller" if sender_id in seller_uids else "creator",
                        "ext": m_ext,
                    }

                setting_info = ci.get("conversation_setting_info", {})
                status_info = ci.get("conversation_status_info", {}) or ci.get("status_info", {})
                read_index = (
                    conv.get("read_index")
                    or ci.get("read_index")
                    or ci.get("read_index_v2")
                    or status_info.get("read_index")
                    or status_info.get("read_order")
                    or setting_info.get("read_index", 0)
                    or setting_info.get("read_index_v2", 0)
                )

                # Extract unread count from TikTok IM response or message stack
                raw_unread = (
                    conv.get("unread_count")
                    or ci.get("unread_count")
                    or core_info.get("unread_count")
                    or setting_info.get("unread_count")
                )

                try:
                    parsed_unread = int(raw_unread) if raw_unread is not None else 0
                except (ValueError, TypeError):
                    parsed_unread = 0

                # TikTok's raw unread_count is unreliable when last message is from seller
                # or is a notification — it counts seller's own messages as unread when
                # read_index is 0 (i.e. seller never explicitly marked the conv as read).
                last_is_seller_or_notif = last_msg is not None and (
                    last_msg.get("sender_role") == "seller"
                    or last_msg.get("ext", {}).get("type") in ("notification", "system", "notice")
                    or last_msg.get("ext", {}).get("s:do_not_increase_unread") == "1"
                )

                if parsed_unread > 0 and not last_is_seller_or_notif:
                    unread_count = parsed_unread
                else:
                    # Count actual unread messages from creator in message list
                    unread_msgs_count = 0
                    for m in msgs:
                        s_raw = m.get("sender", "")
                        s_id = str(s_raw) if isinstance(s_raw, int) else str(s_raw.get("uid", "")) if isinstance(s_raw, dict) else ""
                        m_idx = m.get("index_in_conversation", 0)
                        m_ext_check = m.get("ext") or {}
                        is_notif = m_ext_check.get("type") in ("notification", "system", "notice")
                        do_not_count = m_ext_check.get("s:do_not_increase_unread") == "1"
                        if s_id and s_id not in seller_uids and m_idx > read_index and not is_notif and not do_not_count:
                            unread_msgs_count += 1
                    unread_count = unread_msgs_count


                conv_short_id_str = str(ci.get("conversation_short_id", ""))
                final_avatar = avatar or core_info.get("icon") or ""
                conversations.append({
                    "conversation_short_id": conv_short_id_str,
                    "conversation_type": ci.get("conversation_type", 2),
                    "creator_oec_id": str(creator_oec_id or ""),
                    "name": creator_name,
                    "avatar": final_avatar,
                    "last_message": last_msg,
                    "unread_count": unread_count,
                    "read_index": 0,
                    "create_time": ci.get("create_time") or core_info.get("info_version") or 0,
                })

        return {
            "conversations": conversations,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "total_count": total,
        }
    except Exception as e:
        return {"conversations": [], "has_more": False, "next_cursor": 0, "total_count": 0, "error": str(e)}


def conversation_counts(api: ShopApi, token_data: Dict[str, Any]) -> Dict[int, int]:
    """Fetch conversation total counts for all 5 folder tabs in parallel (limit=1 = ultra lightweight badge query)."""
    token = token_data.get("token", "")
    user_info = token_data.get("user", {})
    uid = str(user_info.get("user_id", ""))

    groups = [(0, "s_all"), (1, "s_unread"), (2, "s_un_reply"), (3, "s_archived"), (4, "s_star")]
    counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    headers = protobuf_headers(token)

    def fetch_one(g_id: int, g_name: str):
        inner = bytearray()
        inner += _ld(1, _vf(1, int(uid) if uid.isdigit() else 0) + _s(2, g_name))
        inner += _vf(2, 0)
        inner += _vf(3, 0)
        inner += _vf(4, 1)  # limit=1 returns exact total_count without loading conversation list
        req_body = _ld(690, _ld(3, bytes(inner)))
        req = encode_request(690, req_body, token, uid)
        raw = api.send("POST", f"{tt.IM_API}/v1/conversation/get_group_list", req, headers)
        top = json.loads(raw.decode())
        body = top.get("body", {}).get("get_conversation_group_list_body", {})
        data = body.get("data", [])
        return g_id, data[0].get("total_count", 0) if data else 0

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_one, g_id, g_name): g_id for g_id, g_name in groups}
        for fut in as_completed(futures):
            try:
                g_id, count = fut.result()
                counts[g_id] = count
            except Exception:
                pass

    return counts


def unread_count(api: ShopApi, token_data: Dict[str, Any]) -> int:
    """Single protobuf request for s_unread total_count. Lightweight sidebar badge."""
    token = token_data.get("token", "")
    uid = str(token_data.get("user", {}).get("user_id", ""))
    headers = protobuf_headers(token)
    inner = bytearray()
    inner += _ld(1, _vf(1, int(uid) if uid.isdigit() else 0) + _s(2, "s_unread"))
    inner += _vf(2, 0)
    inner += _vf(3, 0)
    inner += _vf(4, 1)
    req_body = _ld(690, _ld(3, bytes(inner)))
    req = encode_request(690, req_body, token, uid)
    raw = api.send("POST", f"{tt.IM_API}/v1/conversation/get_group_list", req, headers)
    top = json.loads(raw.decode())
    body = top.get("body", {}).get("get_conversation_group_list_body", {})
    data = body.get("data", [])
    return data[0].get("total_count", 0) if data else 0


class ImError(RuntimeError):
    """TikTok refused an IM call (message is TikTok's own when it gave one)."""


def create_conversation(api: ShopApi, token_data: Dict[str, Any], creator_oec_id: str, seller_id: str) -> str:
    """Open (or find) the conversation between the shop and a creator; its conversation_short_id."""
    body = {"participants": [
        {"role": 0, "uid": str(creator_oec_id), "extra": {"sender_im_role": "4"}},
        {"role": 1, "uid": str(seller_id), "extra": {"sender_im_role": "2"}},
    ]}
    q = {**api.common_query(), "biz_source": "shop_creator_shop", "oec_seller_id": str(seller_id)}
    url = f"{tt.IM_API}/api/v1/im/conversation/create?{urllib.parse.urlencode(q)}"
    res = decode_body(api.send("POST", url, json.dumps(body, separators=(",", ":")),
                               json_headers(token_data.get("token", ""))))
    if not isinstance(res, dict):
        raise ImError("Invalid response from TikTok IM conversation create")
    if res.get("code") != 0 and res.get("code") is not None:
        detail = res.get("message") or f"TikTok IM error code {res.get('code')}"
        raise ImError(f"Failed to create conversation: {detail}")
    inner = res.get("data", {}) if isinstance(res.get("data"), dict) else {}
    cid = inner.get("conversation_short_id") or res.get("conversation_short_id", "")
    if not cid:
        raise ImError("TikTok IM API did not return conversation_short_id")
    return str(cid)


def _send(api: ShopApi, token_data: Dict[str, Any], body: bytes, cid: str, **extra: Any) -> Dict[str, Any]:
    """POST one message (cmd 100) and read TikTok's answer."""
    token = token_data.get("token", "")
    user_id = str(token_data.get("user", {}).get("user_id", ""))
    req = encode_request(100, _ld(100, body), token, user_id)
    raw = api.send("POST", f"{tt.IM_API}/v1/message/send", req, protobuf_headers(token))
    try:
        top = json.loads(raw.decode())
        b_body = top.get("body", {}).get("send_message_body", {})
        return {
            "cmd": top.get("cmd"),
            "status": top.get("status_code"),
            "sent": top.get("status_code") == 0,
            "error_desc": top.get("error_desc"),
            "server_message_id": str(b_body.get("server_message_id") or ""),
            "client_message_id": cid,
            **extra,
        }
    except Exception:
        return {"cmd": None, "status": -1, "sent": False, "raw": raw.hex()}


def send_text(api: ShopApi, token_data: Dict[str, Any], conv_short_id: str, content: str,
              region: str = tt.REGION, user_language: str = "en") -> Dict[str, Any]:
    """Send a text message into a conversation (protobuf cmd 100)."""
    body, cid = encode_send_message(str(conv_short_id or ""), str(content or ""), token_data or {},
                                    region=region, user_language=user_language)
    return _send(api, token_data or {}, body, cid)


def send_image(api: ShopApi, token_data: Dict[str, Any], conv_short_id: str, image_url: str,
               width: int = 1024, height: int = 1024, region: str = tt.REGION,
               user_language: str = "en") -> Dict[str, Any]:
    """Send an uploaded image (affiliate.chat.upload_image) into a conversation."""
    body, cid = encode_send_image_message(str(conv_short_id or ""), image_url, token_data or {}, width, height,
                                          region=region, user_language=user_language)
    return _send(api, token_data or {}, body, cid, image_url=image_url)


def send_product(api: ShopApi, token_data: Dict[str, Any], conv_short_id: str, product_id: str,
                 product_name: str = "", product_image: str = "", region: str = tt.REGION,
                 user_language: str = "en") -> Dict[str, Any]:
    """Send a product card into a conversation."""
    body, cid = encode_send_product_message(str(conv_short_id or ""), product_id, product_name, product_image,
                                            token_data or {}, region=region, user_language=user_language)
    return _send(api, token_data or {}, body, cid, product_id=product_id)


def messages(api: ShopApi, token_data: Dict[str, Any], conversation_short_id: str, cursor: int = 0,
             limit: int = 20) -> Dict[str, Any]:
    """Fetch messages in conversation via Protobuf wire (cmd 301)."""
    token = token_data.get("token", "")
    user_id = str(token_data.get("user", {}).get("user_id", ""))

    inner = bytearray()
    inner += _s(1, str(conversation_short_id))
    inner += _vf(2, 2)  # conversation_type
    inner += _vf(3, int(conversation_short_id) if str(conversation_short_id).isdigit() else 0)
    inner += _vf(4, 1)  # direction
    inner += _vf(5, cursor)
    inner += _vf(6, limit)

    req_body = _ld(301, bytes(inner))
    req = encode_request(301, req_body, token, user_id)
    headers = protobuf_headers(token)
    raw = api.send("POST", f"{tt.IM_API}/v1/message/get_by_conversation", req, headers)

    try:
        top = json.loads(raw.decode())
        body = top.get("body", {}).get("messages_in_conversation_body", {})
        messages = []
        for msg in body.get("messages", []):
            msg_dict = dict(msg)
            # Ensure large 64-bit integers are stringified for JSON/JS precision
            if "server_message_id" in msg_dict and isinstance(msg_dict["server_message_id"], (int, float)):
                msg_dict["server_message_id"] = str(msg_dict["server_message_id"])
            if "sender" in msg_dict and isinstance(msg_dict["sender"], (int, float)):
                msg_dict["sender"] = str(msg_dict["sender"])
            if "conversation_id" in msg_dict and isinstance(msg_dict["conversation_id"], (int, float)):
                msg_dict["conversation_id"] = str(msg_dict["conversation_id"])
        messages = [
            {
                "message_id": str(msg.get("server_message_id") or msg.get("client_message_id", "")),
                "server_message_id": str(msg.get("server_message_id") or ""),
                "sender_id": str(msg.get("sender", "")),
                "sender": str(msg.get("sender", "")),
                "sender_role": "seller" if str(msg.get("sender_im_role") or msg.get("ext", {}).get("sender_im_role", "")) in ("2", "user") or str(msg.get("sender_role", "")) in ("2", "seller") else "creator",
                "sender_name": msg.get("ext", {}).get("uname") or msg.get("ext", {}).get("sender_name") or "",
                "content": msg.get("content", ""),
                "message_type": msg.get("ext", {}).get("type") or msg.get("message_type", 0),
                "image_url": msg.get("ext", {}).get("imageUrl") or msg.get("ext", {}).get("image_url", ""),
                "create_time": msg.get("create_time", 0),
                "index_in_conversation": msg.get("index_in_conversation", 0),
                "status": msg.get("status", 0),
                "ext": msg.get("ext", {}),
            }
            for msg in body.get("messages", [])
        ]
        # Reverse to chronological order (oldest first, newest last) for chat display.
        messages.reverse()

        # Find latest CREATOR message index (seller only marks creator messages as read)
        latest_creator_msg_index = 0
        for msg in reversed(messages):
            if msg.get("sender_role") == "creator":
                latest_creator_msg_index = msg.get("index_in_conversation") or (msg.get("create_time", 0) * 1000)
                break

        # Peer's genuine read index returned by TikTok
        tiktok_read_index = body.get("read_index", 0) or body.get("read_index_v2", 0)
        final_read_index = int(tiktok_read_index or 0)

        return {
            "conversation_short_id": str(conversation_short_id),
            "messages": messages,
            "read_index": final_read_index,
            "creator_latest_index": latest_creator_msg_index,
            "has_more": body.get("has_more", False),
            "next_cursor": body.get("next_cursor", 0),
        }
    except Exception as e:
        return {"conversation_short_id": str(conversation_short_id), "messages": [], "read_index": 0, "has_more": False, "error": str(e)}


def search(api: ShopApi, token_data: Dict[str, Any], query: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """Search conversations across ALL shop history using TikTok's search_conversation_by_users API."""
    token = token_data.get("token", "")

    common_query = api.common_query()

    page_no = max(0, page - 1)
    body_dict = {
        "user_name": str(query).strip(),
        "page_no": page_no,
        "page_size": page_size,
    }

    headers = json_headers(token)

    try:
        url = f"{tt.IM_API}/api/v1/im/search/search_conversation_by_users?{urllib.parse.urlencode(common_query)}"
        raw = api.send("POST", url, json.dumps(body_dict, separators=(",", ":")), headers)
        resp = json.loads(raw.decode())
        if resp.get("code") != 0:
            return {"results": [], "total": 0, "has_more": False, "error": resp.get("message")}

        data = resp.get("data", {})
        matched_convs = data.get("matched_conversations", [])
        core_infos = data.get("core_infos", {})
        setting_infos = data.get("setting_infos", {})
        latest_msgs = data.get("latest_msgs", {})

        conversations = []
        for m in matched_convs:
            cid = str(m.get("conv_short_id") or "")
            if not cid:
                continue

            core = core_infos.get(cid, {})
            biz_ext_raw = core.get("biz_ext", "")
            creator_oec_id = ""
            handle = ""
            avatar = ""
            if biz_ext_raw:
                try:
                    b_dec = json.loads(biz_ext_raw) if isinstance(biz_ext_raw, str) else biz_ext_raw
                    creator_oec_id = b_dec.get("creator_oec_id", "")
                    handle = b_dec.get("handle", "")
                    avatar = b_dec.get("avatar", "")
                except Exception:
                    pass

            if not creator_oec_id:
                creator_oec_id = core.get("ext", {}).get("creator_oec_id", "")

            user_names = []
            for u in m.get("user_id_to_user_names", []):
                user_names.extend(u.get("user_names", []))
            name = handle or (user_names[0] if user_names else "") or "Creator"

            setting = setting_infos.get(cid, {})
            read_index_raw = setting.get("ext", {}).get("paas:read_index", "0")
            read_index = int(read_index_raw) if str(read_index_raw).isdigit() else 0

            last_msg = None
            msg_obj = latest_msgs.get(cid)
            if msg_obj:
                last_msg = {
                    "message_id": str(msg_obj.get("server_message_id", "")),
                    "content": str(msg_obj.get("content", "")),
                    "create_time": int(msg_obj.get("create_time", 0)),
                }

            create_time = int(core.get("create_time") or m.get("last_msg_time") or 0)

            conversations.append({
                "conversation_short_id": cid,
                "conversation_id": cid,
                "creator_oec_id": str(creator_oec_id),
                "name": name,
                "handle": handle,
                "avatar": avatar,
                "unread_count": 0,
                "read_index": read_index,
                "create_time": create_time,
                "last_message": last_msg,
                "tags": m.get("tags", []),
                "stick_on_top": setting.get("is_stick_top", False),
                "is_star": setting.get("is_set_favorite", False),
            })

        return {
            "results": conversations,
            "total": len(conversations),
            "has_more": data.get("has_more", False),
        }
    except Exception as e:
        logger.error(f"Failed to search conversations for query '{query}': {e}", exc_info=True)
        return {"results": [], "total": 0, "has_more": False, "error": str(e)}


def mark_read(api: ShopApi, token_data: Dict[str, Any], conv_short_id: str, read_index: int = 0) -> Dict[str, Any]:
    """Mark messages as read via Protobuf wire (cmd 604 / CMD 2002)."""
    token = token_data.get("token", "")
    user_id = str(token_data.get("user", {}).get("user_id", ""))

    cid_int = int(conv_short_id) if str(conv_short_id).isdigit() else 0

    read_idx = int(read_index) if read_index else int(time.time() * 1000)
    if read_idx > 0 and read_idx < 10000000000:
        read_idx = read_idx * 1000000
    elif read_idx > 0 and read_idx < 10000000000000:
        read_idx = read_idx * 1000

    inner = bytearray()
    inner += _s(1, str(conv_short_id))
    inner += _vf(2, cid_int)
    inner += _vf(3, 2)  # conversation_type = 2 (seller-creator 1-on-1)
    inner += _vf(4, read_idx)
    inner += _vf(5, 0)
    inner += _vf(6, 0)
    inner += _vf(11, 0)

    req_body = _ld(604, bytes(inner))
    req = encode_request(604, req_body, token, user_id)
    headers = protobuf_headers(token)
    raw = api.send("POST", f"{tt.IM_API}/v1/conversation/mark_read", req, headers)

    try:
        top = json.loads(raw.decode())
        return {
            "cmd": top.get("cmd"),
            "status": top.get("status_code"),
            "marked": top.get("status_code") == 0,
            "read_index": read_idx,
            "error_desc": top.get("error_desc"),
        }
    except Exception as e:
        return {"status": "marked_as_read", "read_index": read_idx, "error": str(e)}
