"""Frontier, the IM push websocket: handshake URL, client frames, and decoding push frames
(protobuf wire format) into clean message dicts.

Grounded in a real captured frame (see tests/test_frontier_decode.py), NOT guessed.

Frame layout (verified):
  WsFrame:  f3=service, f4=method, f5=headers(repeated {f1=key,f2=value}),
            f7=payload_encoding ("pb"), f8=payload(bytes)
  payload:  f1=cmd (500 = new-message push, 610 = ack/response), f6=body(bytes)
  body:     f<cmd> (sub-message whose field number == cmd) ; for cmd 500 -> f500
  f500:     f2=conversation_short_id, f5=message
  message:  f1=conversation_short_id, f3=server_message_id, f4=create_time(µs),
            f7=sender_id, f8=content(text), f9=ext(repeated {f1=key,f2=value}),
            f10=create_time(ms)

Only the generic protobuf wire format is used (no .proto / generated stubs), so an added
field never breaks decoding — unknown fields are ignored.
"""
import hashlib
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .. import constants as tt
from .protobuf import _ld, _s, _vf

NEW_MESSAGE_CMD = 500


def _read_varint(b: bytes, i: int):
    shift = 0
    result = 0
    while True:
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            break
    return result, i


def parse_fields(b: bytes) -> Dict[int, List[Any]]:
    """Parse one protobuf message into {field_number: [values]}.

    Length-delimited values are returned as raw bytes (caller decides str vs nested).
    Varints as int, fixed32/64 as int. Unknown/trailing garbage stops parsing.
    """
    out: Dict[int, List[Any]] = {}
    i = 0
    n = len(b)
    while i < n:
        tag, i = _read_varint(b, i)
        fn = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = _read_varint(b, i)
        elif wt == 2:
            ln, i = _read_varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v = int.from_bytes(b[i:i + 4], "little")
            i += 4
        elif wt == 1:
            v = int.from_bytes(b[i:i + 8], "little")
            i += 8
        else:
            break  # groups / unknown wire type: stop, keep what we have
        out.setdefault(fn, []).append(v)
    return out


def _first(fields: Dict[int, List[Any]], fn: int):
    vals = fields.get(fn)
    return vals[0] if vals else None


def _as_text(v: Optional[bytes]) -> str:
    if not v:
        return ""
    try:
        return v.decode("utf-8")
    except Exception:
        return ""


def _kv_map(fields: Dict[int, List[Any]], fn: int) -> Dict[str, str]:
    """Repeated {f1=key, f2=value} entries -> dict."""
    out: Dict[str, str] = {}
    for raw in fields.get(fn, []):
        if not isinstance(raw, (bytes, bytearray)):
            continue
        kv = parse_fields(raw)
        key = _as_text(_first(kv, 1))
        val = _as_text(_first(kv, 2))
        if key:
            out[key] = val
    return out


PARTICIPANTS_READ_INDEX_CMD = 2000
NEW_MESSAGE_CMD = 500
CONV_INFO_PUSH_CMD = 511
SYNC_CONVERSATION_CMD = 610


def decode_new_message(raw: bytes, active_conv_id: str = "", own_im_id: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Return a clean message/event dict for TikTok Frontier binary push & response frames.

    Supported commands:
    - CMD 500: New message push and command_type: 1 read receipt push
    - CMD 511: Conversation badge / group update push (push_type 1, 2, 3, 4)
    - CMD 2000: Live participant read index response (creator & seller read_index)
    - CMD 610: Conversation sync response with setting/status read_index

    None means: unhandled frame (ack/ping/other) — caller should skip it.
    Never raises on a malformed/short frame — returns None so the read loop keeps going.
    """
    try:
        frame = parse_fields(raw)
        payload_raw = _first(frame, 8)
        if not isinstance(payload_raw, (bytes, bytearray)):
            return None
        payload = parse_fields(payload_raw)
        cmd = _first(payload, 1)

        body_raw = _first(payload, 6)
        if not isinstance(body_raw, (bytes, bytearray)):
            return None
        body = parse_fields(body_raw)

        # -------------------------------------------------------------
        # CMD 2000: GetConversationParticipantsReadIndexV3ResponseBody
        # Returns live read_index for every participant in the conversation
        # -------------------------------------------------------------
        if cmd == PARTICIPANTS_READ_INDEX_CMD:
            container_raw = _first(body, PARTICIPANTS_READ_INDEX_CMD)
            if not isinstance(container_raw, (bytes, bytearray)):
                return None
            container = parse_fields(container_raw)
            participant_entries = container.get(1, [])
            participant_indices = {}
            for p_raw in participant_entries:
                if isinstance(p_raw, (bytes, bytearray)):
                    p = parse_fields(p_raw)
                    uid_val = _first(p, 1)
                    ri_val = _first(p, 3)
                    if uid_val is not None and ri_val is not None:
                        participant_indices[str(uid_val)] = int(ri_val)

            # Determine creator's read index (non-seller participant)
            own_set = own_im_id if isinstance(own_im_id, (set, list, tuple)) else {str(own_im_id)} if own_im_id else set()
            own_set = {str(x) for x in own_set if x}

            creator_ri = 0
            for u_str, ri in participant_indices.items():
                if not own_set or u_str not in own_set:
                    creator_ri = max(creator_ri, ri)

            if creator_ri == 0:
                # Creator has not read any messages or creator index not found; don't broadcast false read
                return None

            seq_val = _first(payload, 2)
            return {
                "_event_override": "message_read",
                "conversation_short_id": str(active_conv_id or ""),
                "read_index": creator_ri,
                "participant_read_indices": participant_indices,
                "seq_id": seq_val,
            }

        # -------------------------------------------------------------
        # CMD 610: SyncConversation / GetUserConversationList response
        # -------------------------------------------------------------
        if cmd == SYNC_CONVERSATION_CMD:
            container_raw = _first(body, SYNC_CONVERSATION_CMD)
            if not isinstance(container_raw, (bytes, bytearray)):
                return None
            container = parse_fields(container_raw)
            for item in container.get(1, []):
                if isinstance(item, (bytes, bytearray)):
                    p = parse_fields(item)
                    f51 = _first(p, 51)
                    if isinstance(f51, (bytes, bytearray)):
                        p51 = parse_fields(f51)
                        ri = _first(p51, 5)
                        cid_raw = _first(p, 1)
                        cid_str = _as_text(cid_raw) if cid_raw else str(active_conv_id or "")
                        if ri and int(ri) > 0:
                            return {
                                "_event_override": "message_read",
                                "conversation_short_id": cid_str,
                                "read_index": int(ri),
                            }
            return None

        # -------------------------------------------------------------
        # CMD 511: Conversation group / read sync push
        # -------------------------------------------------------------
        if cmd == CONV_INFO_PUSH_CMD:
            container_raw = _first(body, CONV_INFO_PUSH_CMD)
            if not isinstance(container_raw, (bytes, bytearray)):
                return None
            container = parse_fields(container_raw)
            f3_raw = _first(container, 3)
            if isinstance(f3_raw, (bytes, bytearray)):
                import json
                sync_data = json.loads(f3_raw.decode("utf-8"))
                push_type = sync_data.get("push_type")
                conv_short_id = str(sync_data.get("conversation_short_id") or "")

                if push_type in (2, 4):
                    return {
                        "_event_override": "message_read",
                        "conversation_short_id": conv_short_id,
                        "read_index": 0,
                    }
                if push_type == 3:
                    mb = sync_data.get("message_body")
                    if mb and isinstance(mb, dict):
                        cid = str(mb.get("conversation_short_id") or conv_short_id)
                        smid = str(mb.get("server_message_id") or "")
                        idx_conv = int(mb.get("index_in_conversation") or 0)
                        sender = str(mb.get("sender") or "")
                        content = str(mb.get("content") or "")
                        mb_ext = mb.get("ext") or {}
                        return {
                            "conversation_short_id": cid,
                            "conversation_id": str(mb.get("conversation_id") or cid),
                            "conversation_type": mb.get("conversation_type", 2),
                            "server_message_id": smid,
                            "index_in_conversation": idx_conv,
                            "sender_id": sender,
                            "sender_im_id": mb_ext.get("sender_im_id", sender),
                            "sender_name": mb_ext.get("uname", ""),
                            "sender_role_raw": mb_ext.get("sender_role", ""),
                            "message_type": mb_ext.get("type", "text"),
                            "content": content,
                            "create_time": int(mb.get("create_time") or 0),
                            "ext": mb_ext,
                        }
            return None

        # -------------------------------------------------------------
        # CMD 500: New Message Push / Control Command Push
        # -------------------------------------------------------------
        if cmd != NEW_MESSAGE_CMD:
            return None

        container_raw = _first(body, NEW_MESSAGE_CMD)
        if not isinstance(container_raw, (bytes, bytearray)):
            return None
        container = parse_fields(container_raw)
        conv_from_container = _as_text(_first(container, 2))
        msg_raw = _first(container, 5)
        if not isinstance(msg_raw, (bytes, bytearray)):
            return None
        msg = parse_fields(msg_raw)
        ext = _kv_map(msg, 9)

        conversation_short_id = _as_text(_first(msg, 1)) or conv_from_container
        conv_type = _first(msg, 2)
        server_message_id = _first(msg, 3)
        index_in_conv = _first(msg, 4)
        conv_id = _as_text(_first(msg, 5))
        message_type_num = _first(msg, 6)
        sender_id = _first(msg, 7)
        content = _as_text(_first(msg, 8))
        create_time_ms = _first(msg, 10)

        # Filter internal TikTok sync / control frames (e.g. read_index sync, core update)
        if content and content.startswith('{"command_type":'):
            try:
                import json
                cmd_data = json.loads(content)
                cmd_type = cmd_data.get("command_type")
                if cmd_type == 1:
                    cid = str(cmd_data.get("conversation_id") or conversation_short_id or conv_from_container or "")
                    ri = int(cmd_data.get("read_index") or 0)
                    return {
                        "_event_override": "message_read",
                        "conversation_short_id": cid,
                        "read_index": ri,
                    }
            except Exception:
                pass
            return None

        # System/membership control frames emitted when a conversation is created
        if content and ('"added_participant"' in content or '"removed_participant"' in content
                        or '"conversation_core_info"' in content or '"member_change"' in content):
            return None

        # Empty control markers carry no text and no attachment — skip.
        has_attachment = bool(
            ext.get("imageUrl") or ext.get("image_url") or ext.get("url")
            or ext.get("productId") or ext.get("product_id")
        )
        if not content and not has_attachment:
            return None

        msg_type_str = ext.get("type") or ext.get("biz_msg_type") or (str(message_type_num) if message_type_num is not None else "text")

        return {
            "conversation_short_id": str(conversation_short_id),
            "conversation_id": str(conv_id or conversation_short_id),
            "conversation_type": conv_type or 2,
            "server_message_id": str(server_message_id) if server_message_id is not None else "",
            "index_in_conversation": index_in_conv or 0,
            "sender_id": str(sender_id) if sender_id is not None else str(ext.get("sender_im_id", "")),
            "sender_im_id": ext.get("sender_im_id", ""),
            "sender_name": ext.get("uname", ""),
            "sender_role_raw": ext.get("sender_role", ""),
            "message_type": msg_type_str,
            "content": content,
            "create_time": create_time_ms if create_time_ms is not None else (index_in_conv // 1000 if index_in_conv else 0),
            "ext": ext,
        }
    except Exception:
        return None


def to_broadcast_payload(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a decoded message into the frontend `new_message` payload. No persistence —
    the decoded push is broadcast straight to clients; TikTok stays the history source."""
    if msg.get("_event_override") == "message_read":
        return {
            "event": "message_read",
            "data": {
                "conversation_short_id": msg.get("conversation_short_id"),
                "read_index": msg.get("read_index"),
            },
        }

    role = msg.get("sender_role") or "creator"
    ext = msg.get("ext") or {}
    image_url = ext.get("imageUrl") or ext.get("image_url") or ext.get("url") or ext.get("preview_url") or msg.get("image_url")
    avatar = msg.get("avatar") or ext.get("avatar") or ext.get("avatarUrl") or ext.get("avatar_url") or ""
    return {
        "event": "new_message",
        "data": {
            "conversation_short_id": str(msg.get("conversation_short_id", "")),
            "creator_name": msg.get("sender_name"),
            "creator_avatar": avatar,
            "message": {
                "message_id": str(msg.get("server_message_id", "")),
                "server_message_id": str(msg.get("server_message_id", "")),
                "conversation_short_id": str(msg.get("conversation_short_id", "")),
                "conversation_id": str(msg.get("conversation_id", "")),
                "sender_id": str(msg.get("sender_id", "")),
                "sender": str(msg.get("sender_id", "")),
                "sender_role": role,
                "sender_name": msg.get("sender_name") if role == "creator" else "Seller",
                "avatar": avatar,
                "content": msg.get("content", ""),
                "message_type": msg.get("message_type", "text"),
                "image_url": image_url,
                "create_time": msg.get("create_time", 0),
                "index_in_conversation": msg.get("index_in_conversation", 0),
                "status": 0,
                "ext": ext,
            },
        },
    }


def classify_role(msg: Dict[str, Any], own_im_id) -> str:
    """seller vs creator for a decoded IM message.

    PRIMARY signal: ext.sender_im_role — the participant role TikTok stamps on every message,
    set at conversation creation (seller = "2", creator = "4"). This is authoritative and, unlike
    id comparison, correctly tags the seller's OWN broadcast/DM echoes as 'seller' (they are
    authored under the seller participant uid, not the IM token user_id — so id-only matching
    mislabeled them 'creator' and produced fake 'Creator / pesan baru' popups during a broadcast).

    FALLBACK: compare the sender to the shop's own id(s) when sender_im_role is absent.
    own_im_id may be a single id or a set/list of the shop's identifiers."""
    ext = msg.get("ext") or {}
    imr = str(ext.get("sender_im_role") or "").strip()
    if imr == "2":   # seller participant (create_conversation role 1)
        return "seller"
    if imr == "4":   # creator participant (create_conversation role 0)
        return "creator"
    own_ids = own_im_id if isinstance(own_im_id, (set, list, tuple)) else [own_im_id]
    own_ids = {str(x) for x in own_ids if x}
    sender = {str(msg.get("sender_id") or ""), str(msg.get("sender_im_id") or "")}
    if own_ids & sender:
        return "seller"
    return "creator"


# ============================================================================ handshake / frames

def derive_access_key(fp_id: Any, app_key: str, device_id: str) -> str:
    """The access_key Frontier expects for this device, as the web IM SDK computes it.

    Frontier checks the key against the device_id, so a key lifted from one session only
    ever authenticates that one shop. It is derived client-side, not issued, which is why
    every shop can produce its own from the fields its own IM token already carries."""
    return hashlib.md5(f"{fp_id}{app_key}{device_id}{tt.FRONTIER_ACCESS_KEY_SALT}".encode()).hexdigest()


def frontier_url(token_data: Dict[str, Any]) -> str:
    """The Frontier websocket URL for an IM token, with the handshake params the web SDK sends."""
    token = token_data.get("token", "")
    # The SDK uses the IM user_id as the device it authenticates: deviceId == pigeonId.
    user_id = str(token_data.get("user", {}).get("user_id", ""))
    app_id = token_data.get("app_id", tt.IM_APP_ID)
    fp_id = token_data.get("fp_id", tt.IM_FP_ID)
    access_key = token_data.get("access_key") or derive_access_key(
        fp_id, token_data.get("app_key", ""), user_id
    )
    region = token_data.get("shop_region") or token_data.get("region_code") or tt.REGION
    ws_base = token_data.get("ws_url") or tt.FRONTIER_WS

    params = {
        "token": token,
        "aid": str(app_id),
        "fpid": str(fp_id),
        "device_id": user_id,
        "access_key": access_key,
        "device_platform": "web",
        "version_code": tt.FRONTIER_VERSION_CODE,
        "websocket_switch_region": region,
    }
    # Like the web SDK: x-tt-env is the token's own `env` ("prod" in production, read from
    # the live page 25 Sep 2026), plus the boe/ppe test-env flags. We used to send a fixed
    # "canary", which put our socket on TikTok's canary environment.
    env = str(token_data.get("env") or "")
    if env:
        params["x-tt-env"] = env
        if env.startswith("boe_"):
            params["x-use-boe"] = "1"
        elif env.startswith("ppe_"):
            params["x-use-ppe"] = "1"
    return f"{ws_base}?{urllib.parse.urlencode(params)}"


def encode_ws_frame(seq_id: int, payload: bytes) -> bytes:
    """One client frame on the Frontier socket carrying an IM request envelope."""
    b = bytearray()
    b += _vf(1, seq_id)
    b += _vf(2, int(time.time() * 1000))
    b += _vf(3, 20345)
    b += _vf(4, 1)
    b += _s(7, "pb")
    b += _ld(8, payload)
    return bytes(b)
