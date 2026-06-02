from __future__ import annotations

import base64
import json
import os
import random
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from net_utils import safe_urlopen

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.0.0"
CLIENT_VERSION = "131072"
TEXT_CHUNK_LIMIT = 3500


def default_outbox_dir() -> Path:
    return Path(os.environ.get("COWAGENT_OUTBOX_DIR", str(Path.home() / ".ashare_t_signal" / "cowagent_outbox")))


def default_credentials_path() -> Path:
    return Path(os.environ.get("COWAGENT_WEIXIN_CREDENTIALS", str(Path.home() / ".weixin_cow_credentials.json"))).expanduser()


def _random_wechat_uin() -> str:
    return base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode("utf-8")).decode("utf-8")


def _headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": CLIENT_VERSION,
        "User-Agent": "AShareTSignalMonitor/1.0",
    }


def _api_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/ilink/bot/sendmessage"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_payload(payload: dict) -> str:
    title = str(payload.get("title") or "A股提醒").strip()
    content = str(payload.get("content") or "").strip()
    created_at = str(payload.get("created_at") or "").strip()
    lines = [f"【{title}】"]
    if content:
        lines.append(content)
    if created_at:
        lines.append("")
        lines.append(f"来源：A股监控｜{created_at}")
    return "\n".join(lines).strip()


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


def load_weixin_targets(credentials_path: Path | None = None) -> tuple[str, str, dict[str, str]]:
    path = credentials_path or default_credentials_path()
    if not path.exists():
        raise RuntimeError(f"未找到 CowAgent 微信凭证：{path}。请先启动 CowAgent 并完成微信扫码登录。")
    creds = _read_json(path)
    token = str(creds.get("token") or "").strip()
    if not token:
        raise RuntimeError(f"CowAgent 微信凭证缺少 token：{path}")
    base_url = str(creds.get("base_url") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    context_tokens = creds.get("context_tokens") or {}
    if not isinstance(context_tokens, dict) or not context_tokens:
        raise RuntimeError("CowAgent 还没有可推送的微信会话。请先在微信里给 CowAgent 发一句话，然后再启动桥接。")
    targets = {str(k): str(v) for k, v in context_tokens.items() if str(k) and str(v)}
    if not targets:
        raise RuntimeError("CowAgent 微信 context_tokens 为空。请先在微信里给 CowAgent 发一句话。")
    receiver = os.environ.get("COWAGENT_WEIXIN_RECEIVER", "").strip()
    if receiver:
        if receiver not in targets:
            raise RuntimeError(f"指定的 COWAGENT_WEIXIN_RECEIVER 不在 CowAgent 会话里：{receiver}")
        targets = {receiver: targets[receiver]}
    return token, base_url, targets


def send_weixin_text(text: str, token: str, base_url: str, receiver: str, context_token: str) -> None:
    for chunk in _split_text(text):
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
        req = urllib.request.Request(
            _api_url(base_url),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=_headers(token),
            method="POST",
        )
        with safe_urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        ret = result.get("ret", result.get("errcode", 0))
        if ret in (-14, "-14"):
            raise RuntimeError("CowAgent 微信会话已过期，请在微信里给 CowAgent 再发一句话刷新会话。")
        if ret not in (0, "0", None) and result.get("errcode") not in (0, "0", None):
            raise RuntimeError(f"CowAgent 微信发送失败：{result}")
        time.sleep(0.2)


class CowAgentOutboxBridge:
    def __init__(
        self,
        outbox_dir: Path | None = None,
        credentials_path: Path | None = None,
        interval: int = 3,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.outbox_dir = outbox_dir or default_outbox_dir()
        self.credentials_path = credentials_path or default_credentials_path()
        self.interval = max(1, interval)
        self.logger = logger or print
        self.sent_dir = self.outbox_dir / "sent"
        self.failed_dir = self.outbox_dir / "failed"

    def ensure_dirs(self) -> None:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.sent_dir.mkdir(exist_ok=True)
        self.failed_dir.mkdir(exist_ok=True)

    def process_once(self) -> int:
        self.ensure_dirs()
        paths = sorted(self.outbox_dir.glob("*.json"))
        if not paths:
            return 0
        token, base_url, targets = load_weixin_targets(self.credentials_path)
        processed = 0
        for path in paths:
            try:
                payload = _read_json(path)
                text = _format_payload(payload)
                for receiver, context_token in targets.items():
                    send_weixin_text(text, token, base_url, receiver, context_token)
                target = self.sent_dir / path.name
                if target.exists():
                    target = self.sent_dir / f"{path.stem}-{datetime.now().strftime('%H%M%S')}{path.suffix}"
                path.replace(target)
                processed += 1
                self.logger(f"CowAgent 已推送：{path.name}")
            except Exception as exc:
                self.logger(f"CowAgent 推送失败：{path.name}｜{exc}")
                target = self.failed_dir / path.name
                if target.exists():
                    target = self.failed_dir / f"{path.stem}-{datetime.now().strftime('%H%M%S')}{path.suffix}"
                path.replace(target)
        return processed

    def run_forever(self, stop: Callable[[], bool] | None = None) -> None:
        self.ensure_dirs()
        self.logger(f"CowAgent outbox 桥接已启动：{self.outbox_dir}")
        while not (stop and stop()):
            try:
                self.process_once()
            except Exception as exc:
                self.logger(f"CowAgent 桥接等待中：{exc}")
            time.sleep(self.interval)


def main() -> None:
    interval = int(os.environ.get("COWAGENT_BRIDGE_INTERVAL", "3"))
    bridge = CowAgentOutboxBridge(interval=interval)
    bridge.run_forever()


if __name__ == "__main__":
    main()
