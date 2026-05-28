from __future__ import annotations

import argparse
import os
import sys
import time
from getpass import getpass
from pathlib import Path

from notifier import PushPlusNotifier
from strategy import build_engine


DEFAULT_INTERVAL_SECONDS = 30


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def prompt_token() -> str:
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if token:
        return token

    token = getpass("请输入 PushPlus token：").strip()
    if not token:
        raise SystemExit("PushPlus token 不能为空。")
    return token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股做T信号微信推送监控")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("SIGNAL_CHECK_INTERVAL", str(DEFAULT_INTERVAL_SECONDS))),
        help="检查间隔秒数，默认 30。",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="启动时先发送一条 PushPlus 测试消息。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = prompt_token()
    os.environ["PUSHPLUS_TOKEN"] = token

    root = app_dir()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print("A股做T信号监控已启动")
    print("监控标的：剑桥科技、东山精密")
    print("提醒窗口：北京时间 09:45-10:30 / 14:00-14:30")
    print(f"检查间隔：{args.interval}s")
    print("按 Ctrl+C 退出")

    if args.test:
        PushPlusNotifier(token).send_text("做T提醒测试：桌面监控程序已接通。", title="做T提醒测试")
        print("PushPlus 测试消息已发送")

    engine = build_engine(data_dir)
    while True:
        try:
            result = engine.check_all()
            alerts = result.get("alerts", [])
            checked_at = result.get("checked_at")
            print(f"[{checked_at}] checked, new_alerts={len(alerts)}", flush=True)
            for alert in alerts:
                print(
                    f"  pushed: {alert.get('symbol')} {alert.get('minute')} "
                    f"entry={alert.get('entry_price')} exit={alert.get('exit_price')}",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("\n已退出。")
            break
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
