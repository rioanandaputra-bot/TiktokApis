"""IM web SDK protobuf: the request envelope and the send-message bodies.

Wire format only (no .proto): field numbers and ext keys as the seller/im page's SDK sends
them. `region` and `user_language` are the shop's, passed in by the caller.
"""
import itertools
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

from .. import constants as tt


# Envelope sequence numbers, one counter per IM device (= shop), like each browser tab's
# SDK keeps its own. A single process-wide list was shared by every shop and not thread-safe.
_SEQ_START = 10001
_seqs: Dict[str, "itertools.count[int]"] = {}
_seq_lock = threading.Lock()


def _next_seq(device_id: str) -> int:
    with _seq_lock:
        counter = _seqs.get(device_id)
        if counter is None:
            counter = _seqs[device_id] = itertools.count(_SEQ_START)
        return next(counter)


# ============================================================================
# PROTOBUF WIRE ENCODERS
# ============================================================================

def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(f: int, w: int) -> bytes:
    return _varint((f << 3) | w)


def _ld(f: int, payload: bytes) -> bytes:
    return _tag(f, 2) + _varint(len(payload)) + payload


def _vf(f: int, n: int) -> bytes:
    return _tag(f, 0) + _varint(n)


def _s(f: int, s: str) -> bytes:
    return _ld(f, s.encode())


def _map_entry(k: str, v: str) -> bytes:
    return _s(1, k) + _s(2, v)


def _strmap(f: int, m: Dict[str, str]) -> bytes:
    return b"".join(_ld(f, _map_entry(k, v)) for k, v in m.items())


def encode_request(cmd: int, body: bytes, token: str, device_id: str, seq_id: Optional[int] = None) -> bytes:
    """Encode binary Protobuf request envelope."""
    seq_num = _next_seq(str(device_id)) if seq_id is None else seq_id

    req = bytearray()
    req += _vf(1, cmd)
    req += _vf(2, seq_num)
    req += _s(3, tt.IM_SDK_VERSION)
    req += _s(4, token)
    req += _vf(5, 3)
    req += _vf(6, 0)
    req += _s(7, tt.IM_BUILD_NUMBER)
    req += _ld(8, body)
    req += _s(9, device_id)
    req += _s(11, "web")
    req += _vf(18, 2)
    return bytes(req)


def encode_send_message(short_id: str, text: str, token: Dict[str, Any], region: str = tt.REGION,
                        user_language: str = "en") -> Tuple[bytes, str]:
    """Encode send text message Protobuf payload."""
    b = bytearray()
    b += _s(1, str(short_id))
    b += _vf(2, 2)
    b += _vf(3, int(short_id) if str(short_id).isdigit() else 0)
    b += _s(4, text)

    cid = str(uuid.uuid4())
    region = token.get("shop_region") or token.get("region_code") or region
    user_lang = token.get("user_language") or user_language

    ext = {
        "type": "text",
        "sender_im_role": "2",
        "sender_im_id": str(token.get("user", {}).get("user_id", "")),
        "PIGEON_BIZ_TYPE": "1",
        "shop_region": region,
        "s:biz_aid": str(token.get("app_id", "")),
        "a:user_language": user_lang,
        "s:device_platform": "web",
        "s:msg_grade": "normal",
        "s:sub_scene": "default",
        "s:base_scene": "default",
        "monitor_send_message_platform": "pc",
        "s:client_message_id": cid,
    }
    b += _strmap(5, ext)
    b += _vf(6, 7)
    b += _s(8, cid)
    return bytes(b), cid


def encode_send_product_message(
    short_id: str,
    product_id: str,
    product_name: str,
    product_image: str,
    token: Dict[str, Any] = None,
    region: str = tt.REGION,
    user_language: str = "en",
) -> Tuple[bytes, str]:
    """Encode send product card message Protobuf payload matching TikTok IM HAR Entry 188."""
    import json as _json
    if token is None:
        token = {}
    cid = str(uuid.uuid4())

    region = token.get("shop_region") or token.get("region_code") or region
    user_lang = token.get("user_language") or user_language
    sender_im_id = str(token.get("user", {}).get("user_id", ""))
    sender_name = str(token.get("shop_name") or token.get("user", {}).get("name", ""))

    b = bytearray()
    b += _s(1, str(short_id))
    b += _vf(2, 2)
    b += _vf(3, int(short_id) if str(short_id).isdigit() else 0)
    b += _s(4, "[Product card]")

    search_ctx = {
        "sender_name": sender_name,
        "biz_msg_type": "product_card",
        "search_content": product_name,
    }

    ext = {
        "s:client_message_id": cid,
        "shop_region": region,
        "a:user_language": user_lang,
        "monitor_send_message_platform": "pc",
        "s:is_stranger": "false",
        "s:sub_scene": "default",
        "s:msg_grade": "normal",
        "s:is_parallel_conv_gray": "true",
        "starling_content_key": "im_creator_message_type_product_card",
        "s:device_platform": "web",
        "b:oec_im_search_context": _json.dumps(search_ctx),
        "PIGEON_BIZ_TYPE": "1",
        "sender_im_role": "2",
        "sender_role": "2",
        "s:base_scene": "default",
        "type": "product",
        "s:biz_aid": str(token.get("app_id") or tt.IM_APP_ID),
        "sender_im_id": sender_im_id,
        "productId": str(product_id),
        "product_id": str(product_id),
        "product_name": product_name,
        "product_image": product_image,
    }
    b += _strmap(5, ext)
    b += _vf(6, 7)
    b += _s(8, cid)
    return bytes(b), cid


def encode_send_image_message(short_id: str, image_url: str, token: Dict[str, Any] = None, width: int = 1024,
                              height: int = 1024, region: str = tt.REGION, user_language: str = "en") -> Tuple[bytes, str]:
    """Encode send photo/image message Protobuf payload."""
    if token is None:
        token = {}
    b = bytearray()
    b += _s(1, str(short_id))
    b += _vf(2, 2)
    b += _vf(3, int(short_id) if str(short_id).isdigit() else 0)
    b += _s(4, "[Photo]")

    cid = str(uuid.uuid4())
    region = token.get("shop_region") or token.get("region_code") or region
    user_lang = token.get("user_language") or user_language

    ext = {
        "type": "file_image",
        "imageUrl": image_url,
        "imageWidth": str(width or 1024),
        "imageHeight": str(height or 1024),
        "starling_content_key": "im_sdk_cell_sent_photo",
        "sender_im_role": "2",
        "sender_role": "2",
        "sender_im_id": str(token.get("user", {}).get("user_id", "")),
        "PIGEON_BIZ_TYPE": "1",
        "shop_region": region,
        "s:biz_aid": str(token.get("app_id", "")),
        "a:user_language": user_lang,
        "s:device_platform": "web",
        "s:msg_grade": "normal",
        "s:sub_scene": "default",
        "s:base_scene": "default",
        "monitor_send_message_platform": "pc",
        "s:client_message_id": cid,
        "is_allocated_event": "1",
    }
    b += _strmap(5, ext)
    b += _vf(6, 7)
    b += _s(8, cid)
    return bytes(b), cid
