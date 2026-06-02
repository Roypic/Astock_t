from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from net_utils import safe_urlopen


def format_signal(signal: dict[str, Any]) -> str:
    lines = [
        f"做T信号：{signal['symbol']} {signal['code']}",
        f"时间：{signal['trade_day']} {signal['minute']}（{signal.get('window', '入场')}）",
        f"评分：{signal.get('signal_score', '-')}",
        f"趋势：{signal.get('trend_mode', '-')}（{signal.get('trend_score', '-')}）",
        f"动作：{signal['entry_label']}",
        f"入场价：{signal['entry_price']}",
        f"{signal['exit_label']}：{signal['exit_price']}",
        f"止损价：{signal['stop_price']}",
        f"今日次数：{signal.get('daily_count', '?')}/{signal.get('max_daily_signals', 1)}",
        f"个股涨幅：{signal.get('own_return_pct')}%",
        f"大盘强弱：{signal.get('market_return_pct')}%",
        f"篮子涨幅：{signal.get('basket_return_pct')}%",
        f"篮子分化：{signal.get('basket_dispersion_pct')}%",
        f"相对强弱：{signal.get('relative_return_pct')}%",
        f"MA5/10/20：{signal.get('ma5')}/{signal.get('ma10')}/{signal.get('ma20')}",
        "",
        "执行建议：收到信号后自行决定是否入场；入场后可按目标价/止损价预挂单或设置提醒。",
        "分钟级辅助提醒，不是投资建议。下单前请以券商行情和个人风控为准。",
    ]
    return "\n".join(lines)


class WeComNotifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.environ.get("WECOM_WEBHOOK_URL", "")

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send_text(self, content: str) -> None:
        if not self.enabled:
            return
        payload = {
            "msgtype": "text",
            "text": {"content": content},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with safe_urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("errcode") != 0:
            raise RuntimeError(f"WeCom webhook failed: {result}")

    def send_signal(self, signal: dict[str, Any]) -> None:
        self.send_text(format_signal(signal))


class PushPlusNotifier:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("PUSHPLUS_TOKEN", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def send_text(self, content: str, title: str = "做T提醒") -> None:
        if not self.enabled:
            return
        payload = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": "txt",
            "channel": "wechat",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://www.pushplus.plus/send",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with safe_urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") != 200:
            raise RuntimeError(f"PushPlus failed: {result}")

    def send_signal(self, signal: dict[str, Any]) -> None:
        self.send_text(format_signal(signal), title=f"做T信号：{signal['symbol']}")


class MultiNotifier:
    def __init__(self, notifiers: list[Any]) -> None:
        self.notifiers = notifiers

    @property
    def enabled(self) -> bool:
        return any(getattr(notifier, "enabled", False) for notifier in self.notifiers)

    def send_signal(self, signal: dict[str, Any]) -> None:
        errors = []
        sent = 0
        for notifier in self.notifiers:
            if not getattr(notifier, "enabled", False):
                continue
            try:
                notifier.send_signal(signal)
                sent += 1
            except Exception as exc:
                errors.append(str(exc))
        if sent == 0 and errors:
            raise RuntimeError("; ".join(errors))


def build_notifier() -> MultiNotifier:
    return MultiNotifier(
        [
            PushPlusNotifier(),
            WeComNotifier(),
        ]
    )
