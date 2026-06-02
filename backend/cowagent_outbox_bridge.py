from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from net_utils import safe_urlopen


def post_payload(endpoint: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "AShareTSignalMonitor/1.0"},
        method="POST",
    )
    with safe_urlopen(req, timeout=8) as resp:
        if getattr(resp, "status", 200) >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def main() -> None:
    endpoint = os.environ.get("COWAGENT_WEBHOOK_URL", "").strip()
    if not endpoint:
        raise SystemExit("Please set COWAGENT_WEBHOOK_URL.")
    outbox = Path(os.environ.get("COWAGENT_OUTBOX_DIR", str(Path.home() / ".ashare_t_signal" / "cowagent_outbox")))
    sent = outbox / "sent"
    failed = outbox / "failed"
    outbox.mkdir(parents=True, exist_ok=True)
    sent.mkdir(exist_ok=True)
    failed.mkdir(exist_ok=True)
    interval = max(1, int(os.environ.get("COWAGENT_BRIDGE_INTERVAL", "5")))
    print(f"Watching {outbox}")
    while True:
        for path in sorted(outbox.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                post_payload(endpoint, payload)
                path.replace(sent / path.name)
                print(f"sent {path.name}")
            except Exception as exc:
                print(f"failed {path.name}: {exc}")
                path.replace(failed / path.name)
        time.sleep(interval)


if __name__ == "__main__":
    main()
