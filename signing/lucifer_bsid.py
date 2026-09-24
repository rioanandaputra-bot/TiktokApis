"""Pure local bridge for the official OEC Lucifer (unisec) signer, any page profile."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import threading
from typing import Iterable, Mapping


class LuciferBSIDError(RuntimeError):
    """The local OEC SDK could not produce a BSID or a bs token."""


class LuciferBSIDSigner:
    """Run the immutable official unisec loader/core through ``bsid.js``.

    The page comes from a profile (``reverse/tiktok_shop_bsid/env/profiles``).
    ``mint_token`` boots the SDK once for a cookie session and lets it fetch
    its own bs token from ``/bs/rt``.  ``sign`` keeps one ``bsid.js --serve``
    process alive and signs for any session whose cookie carries that token:
    the SDK itself is booted once, not per request.  No captured BSID or token
    is accepted as a constant; every output is validated before it is returned.
    """

    _TOKEN = re.compile(r"[0-9a-fA-F]{160,}")
    _OEC_COOKIE = re.compile(r"(?:^|;\s*)oec_lucifer=([^;]+)")

    def __init__(self, *, profile: str = "affiliate-id.json",
                 node: str | None = None,
                 script: str | os.PathLike[str] | None = None,
                 timeout: int = 30, sign_timeout: int = 10):
        self.profile = str(profile)
        self.node = node or shutil.which("node") or ""
        self.script = Path(script) if script else (
            Path(__file__).resolve().parents[1]
            / "reverse" / "tiktok_shop_bsid" / "env" / "bsid.js"
        )
        self.timeout = int(timeout)
        self.sign_timeout = int(sign_timeout)
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._sequence = 0

    def _check(self) -> None:
        if not self.node:
            raise LuciferBSIDError("Lucifer BSID 纯算需要本机 Node.js")
        if not self.script.is_file():
            raise LuciferBSIDError("Lucifer BSID 本地运行器不存在")

    def _environment(self, user_agent: str = "") -> dict:
        environment = os.environ.copy()
        environment["TIKTOK_BSID_QUIET"] = "1"
        environment["TIKTOK_BSID_PROFILE"] = self.profile
        if user_agent:
            environment["TIKTOK_BSID_UA"] = str(user_agent)
        return environment

    @staticmethod
    def _requests(requests: Iterable[Mapping[str, str] | tuple[str, str, str]]) -> list[dict]:
        out = []
        for request in requests:
            if isinstance(request, Mapping):
                method, url, body = request.get("method"), request.get("url"), request.get("body")
            else:
                method, url, body = request
            if not url:
                raise LuciferBSIDError("Lucifer BSID 需要完整的签名前 URL")
            out.append({"method": str(method or "GET").upper(), "url": str(url),
                        "body": "" if body is None else str(body)})
        return out

    def mint_token(self, *, cookie: str, user_agent: str = "") -> str:
        """Let the SDK fetch a bs token for this cookie session (one boot)."""
        self._check()
        cookie = self._OEC_COOKIE.sub("", str(cookie or "")).strip("; ")
        payload = {"cookie": cookie, "requests": [
            {"method": "GET", "url": "https://localhost/api/v1/bs/ping", "body": ""}]}
        try:
            completed = subprocess.run(
                [self.node, str(self.script)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True, encoding="utf-8", capture_output=True,
                timeout=self.timeout, check=False, cwd=str(self.script.parent),
                env=self._environment(user_agent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LuciferBSIDError("Lucifer bs token 本地计算进程启动失败") from exc
        if completed.returncode != 0:
            # Vendor failures must not echo stdin/cookies into application logs.
            lines = (completed.stderr or "").strip().splitlines()
            suffix = f"：{lines[-1].strip()[:200]}" if lines else ""
            raise LuciferBSIDError(f"Lucifer bs token 获取失败{suffix}")
        try:
            token = str(json.loads(completed.stdout).get("bs_token") or "")
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise LuciferBSIDError("Lucifer 运行器返回了非 JSON 数据") from exc
        if not self._TOKEN.fullmatch(token):
            raise LuciferBSIDError("Lucifer bs token 格式与 Chrome 不一致")
        return token

    def _start(self) -> None:
        self._process = subprocess.Popen(
            [self.node, str(self.script), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, cwd=str(self.script.parent),
            env=self._environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not self._read(self.timeout).get("ready"):
            self.close()
            raise LuciferBSIDError("Lucifer BSID 常驻进程未就绪")

    def _read(self, timeout: int) -> dict:
        assert self._process is not None and self._process.stdout is not None
        if not select.select([self._process.stdout], [], [], timeout)[0]:
            raise TimeoutError("Lucifer BSID 常驻进程无响应")
        line = self._process.stdout.readline()
        if not line:
            raise LuciferBSIDError("Lucifer BSID 常驻进程已退出")
        return json.loads(line)

    def sign(self, *, cookie: str, user_agent: str = "",
             requests: Iterable[Mapping[str, str] | tuple[str, str, str]]) -> list[str]:
        """BSIDs for the given pre-sign requests, in order.

        Each URL must already carry msToken, X-Bogus and X-Gnarly exactly as
        they will be sent; ``cookie`` must carry the session's ``oec_lucifer``.
        """
        self._check()
        match = self._OEC_COOKIE.search(str(cookie or ""))
        if not match or not self._TOKEN.fullmatch(match.group(1)):
            raise LuciferBSIDError("当前 Cookie 缺少 SDK 生成的 oec_lucifer；请先调用 mint_token")
        batch = self._requests(requests)
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._process is None or self._process.poll() is not None:
                        self._start()
                    self._sequence += 1
                    self._process.stdin.write(json.dumps({
                        "id": self._sequence, "cookie": str(cookie),
                        "ua": str(user_agent or ""), "requests": batch,
                    }, ensure_ascii=False) + "\n")
                    self._process.stdin.flush()
                    result = self._read(self.sign_timeout)
                    if result.get("id") != self._sequence:
                        raise LuciferBSIDError("Lucifer BSID 常驻进程响应顺序错误")
                    if "error" in result:
                        raise ValueError(str(result["error"]))
                    break
                except ValueError as exc:
                    raise LuciferBSIDError(f"Lucifer BSID 计算失败：{exc}") from exc
                except (OSError, TimeoutError, LuciferBSIDError, json.JSONDecodeError):
                    # A dead or stalled runner is restarted once; state lives in the cookie.
                    self.close()
                    if attempt == 2:
                        raise
        bsids = [str(value) for value in result.get("bsids") or []]
        if len(bsids) != len(batch) or not all(re.fullmatch(r"[0-9a-f]+", value) for value in bsids):
            raise LuciferBSIDError("Lucifer BSID 输出格式与 Chrome 不一致")
        return bsids

    def close(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:
                pass
        self._process = None


__all__ = ["LuciferBSIDError", "LuciferBSIDSigner"]
