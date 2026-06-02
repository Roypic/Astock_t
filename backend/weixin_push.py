from __future__ import annotations

import json
import os
import base64
import random
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Callable

from net_utils import safe_urlopen

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
CLIENT_VERSION = "131072"
CHANNEL_VERSION = "2.0.0"
SESSION_EXPIRED_ERRCODE = -14
TEXT_CHUNK_LIMIT = 3500


def default_credentials_path() -> Path:
    return Path(os.environ.get("ASHARE_WEIXIN_CREDENTIALS", str(Path.home() / ".ashare_t_signal" / "weixin_credentials.json"))).expanduser()


def _ensure_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _request_json(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: int = 20) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with safe_urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _random_wechat_uin() -> str:
    return base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode("utf-8")).decode("utf-8")


def _split_text(text: str, limit: int = TEXT_CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines(True):
        if current and current_len + len(line) > limit:
            chunks.append("".join(current))
            current = []
            current_len = 0
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def send_weixin_text(text: str, token: str, base_url: str, receiver: str, context_token: str) -> None:
    for chunk in _split_text(text):
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": _random_wechat_uin(),
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": CLIENT_VERSION,
            "User-Agent": "AShareTSignalMonitor/1.0",
        }
        body = {
            "base_info": {"channel_version": CHANNEL_VERSION},
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver,
                "client_id": uuid.uuid4().hex[:16],
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                "context_token": context_token,
            },
        }
        result = _request_json(_ensure_slash(base_url) + "ilink/bot/sendmessage", method="POST", body=body, headers=headers, timeout=15)
        ret = result.get("ret", result.get("errcode", 0))
        if ret in (-14, "-14"):
            raise RuntimeError("微信会话已过期，请在微信里给这个 bot 再发一句话刷新会话。")
        if ret not in (0, "0", None) and result.get("errcode") not in (0, "0", None):
            raise RuntimeError(f"微信发送失败：{result}")
        time.sleep(0.2)


def fetch_qr_code(base_url: str = DEFAULT_BASE_URL) -> tuple[str, str]:
    url = _ensure_slash(base_url) + f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}"
    data = _request_json(url, timeout=15)
    qrcode = str(data.get("qrcode") or "")
    qrcode_url = str(data.get("qrcode_img_content") or "")
    if not qrcode:
        raise RuntimeError(f"微信登录二维码获取失败：{data}")
    return qrcode, qrcode_url


def poll_qr_status(qrcode: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 10) -> dict:
    url = _ensure_slash(base_url) + "ilink/bot/get_qrcode_status?qrcode=" + urllib.parse.quote(qrcode)
    headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": CLIENT_VERSION}
    try:
        return _request_json(url, headers=headers, timeout=timeout)
    except TimeoutError:
        return {"status": "wait"}


def login_with_qr(
    credentials_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    logger: Callable[[str], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> dict:
    log = logger or print
    path = credentials_path or default_credentials_path()
    qrcode, qrcode_url = fetch_qr_code(base_url)
    log("微信登录二维码已生成，正在打开浏览器")
    log(qrcode_url)
    try:
        webbrowser.open(qrcode_url)
    except Exception:
        pass
    deadline = time.time() + 480
    scanned = False
    while time.time() < deadline and not (stop and stop()):
        status_resp = poll_qr_status(qrcode, base_url=base_url, timeout=10)
        status = status_resp.get("status", "wait")
        if status == "scaned" and not scanned:
            scanned = True
            log("已扫码，请在手机微信上确认登录")
        elif status == "expired":
            log("二维码已过期，正在刷新")
            qrcode, qrcode_url = fetch_qr_code(base_url)
            log(qrcode_url)
            try:
                webbrowser.open(qrcode_url)
            except Exception:
                pass
            scanned = False
        elif status == "confirmed":
            token = str(status_resp.get("bot_token") or "")
            bot_id = str(status_resp.get("ilink_bot_id") or "")
            result_base = str(status_resp.get("baseurl") or base_url)
            user_id = str(status_resp.get("ilink_user_id") or "")
            if not token or not bot_id:
                raise RuntimeError(f"微信登录确认失败，缺少 token/bot_id：{status_resp}")
            creds = _read_json(path)
            creds.update({"token": token, "base_url": result_base, "bot_id": bot_id, "user_id": user_id})
            creds.setdefault("context_tokens", {})
            _save_json(path, creds)
            log(f"微信登录成功，凭证已保存：{path}")
            return creds
        time.sleep(1)
    raise RuntimeError("微信扫码登录超时或已取消")


def get_updates(token: str, base_url: str, cursor: str = "", timeout: int = 20) -> dict:
    url = _ensure_slash(base_url) + "ilink/bot/getupdates"
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": "MA==",
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": CLIENT_VERSION,
        "User-Agent": "AShareTSignalMonitor/1.0",
    }
    body = {"base_info": {"channel_version": CHANNEL_VERSION}, "get_updates_buf": cursor}
    return _request_json(url, method="POST", body=body, headers=headers, timeout=timeout + 5)


def refresh_context_tokens(
    credentials_path: Path | None = None,
    logger: Callable[[str], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> None:
    log = logger or print
    path = credentials_path or default_credentials_path()
    cursor = ""
    while not (stop and stop()):
        creds = _read_json(path)
        token = str(creds.get("token") or "")
        base_url = str(creds.get("base_url") or DEFAULT_BASE_URL)
        if not token:
            time.sleep(3)
            continue
        try:
            resp = get_updates(token, base_url, cursor=cursor, timeout=20)
            ret = resp.get("ret", 0)
            errcode = resp.get("errcode", 0)
            if ret == SESSION_EXPIRED_ERRCODE or errcode == SESSION_EXPIRED_ERRCODE:
                log("微信登录会话已过期，请重新扫码登录")
                time.sleep(10)
                continue
            cursor = str(resp.get("get_updates_buf") or cursor)
            changed = False
            context_tokens = creds.get("context_tokens")
            if not isinstance(context_tokens, dict):
                context_tokens = {}
            for msg in resp.get("msgs", []) or []:
                if msg.get("message_type") != 1:
                    continue
                from_user = str(msg.get("from_user_id") or "")
                context_token = str(msg.get("context_token") or "")
                if from_user and context_token and context_tokens.get(from_user) != context_token:
                    context_tokens[from_user] = context_token
                    changed = True
                    log("已捕获微信推送会话，可以发送提醒")
            if changed:
                creds["context_tokens"] = context_tokens
                _save_json(path, creds)
        except Exception as exc:
            log(f"微信会话刷新等待中：{exc}")
            time.sleep(5)


def load_targets(credentials_path: Path | None = None) -> tuple[str, str, dict[str, str]]:
    path = credentials_path or default_credentials_path()
    creds = _read_json(path)
    token = str(creds.get("token") or "")
    base_url = str(creds.get("base_url") or DEFAULT_BASE_URL)
    targets = creds.get("context_tokens") or {}
    if not token:
        raise RuntimeError("尚未完成内置微信扫码登录")
    if not isinstance(targets, dict) or not targets:
        raise RuntimeError("尚未捕获微信会话。请先在微信里给这个 bot 发一句话。")
    return token, base_url, {str(k): str(v) for k, v in targets.items() if str(k) and str(v)}


def send_text_to_all(content: str, title: str = "A股提醒", credentials_path: Path | None = None) -> None:
    token, base_url, targets = load_targets(credentials_path)
    text = f"【{title}】\n{content}".strip()
    for receiver, context_token in targets.items():
        send_weixin_text(text, token, base_url, receiver, context_token)
