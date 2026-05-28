from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notifier import MultiNotifier, build_notifier


BEIJING_TZ = timezone(timedelta(hours=8))
BASE_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/prices"
INDEX_BASE_URL = "https://market.ft.tech/app/api/v2/indices/{code}/prices"
HEADERS = {
    "X-Client-Name": "ft-claw",
    "Content-Type": "application/json",
}


INDEX_CODES = {
    "上证": "000001.XSHG",
    "深成": "399001.XSHE",
    "创业板": "399006.XSHE",
}


STOCKS = {
    "剑桥科技": "603083.XSHG",
    "东山精密": "002384.SZ",
    "光迅科技": "002281.SZ",
    "华工科技": "000988.SZ",
    "博创科技": "300548.SZ",
    "中际旭创": "300308.SZ",
    "仕佳光子": "688313.XSHG",
    "源杰科技": "688498.XSHG",
    "永鼎股份": "600105.XSHG",
    "长光华芯": "688048.XSHG",
    "太辰光": "300570.SZ",
}


BASKETS = {
    "剑桥科技": ["华工科技", "博创科技", "中际旭创", "东山精密", "仕佳光子", "光迅科技"],
    "东山精密": ["源杰科技", "仕佳光子", "剑桥科技", "永鼎股份", "博创科技", "长光华芯"],
    "光迅科技": ["华工科技", "仕佳光子", "太辰光", "剑桥科技", "博创科技", "东山精密"],
}


# Parameters from the 30-day T+1-aware multi-objective backtest.
# basket_threshold, market_threshold, relative_threshold, avg_threshold, take_profit, stop_loss, max_basket_dispersion
PARAMS = {
    "剑桥科技": (0.012, 0.000, 0.000, 0.006, 0.024, 0.012, 0.040),
    "东山精密": (0.016, 0.006, 0.006, 0.006, 0.024, 0.010, 0.040),
}

ENTRY_WINDOWS = (
    ("早盘", "09:45", "10:30"),
    ("尾盘", "14:00", "14:30"),
)


@dataclass
class MinutePrice:
    day: str
    minute: str
    price: float
    avg_price: float


class MarketClient:
    def __init__(self, ttl_seconds: int = 20) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, list[MinutePrice]]] = {}

    def get_today_prices(self, name: str) -> list[MinutePrice]:
        return self._get_today_prices(name, STOCKS[name], BASE_URL)

    def get_today_index_prices(self, name: str) -> list[MinutePrice]:
        return self._get_today_prices(f"index:{name}", INDEX_CODES[name], INDEX_BASE_URL)

    def get_daily_trend(self, name: str) -> dict[str, Any]:
        prices = self.get_daily_ohlcs(name)
        if len(prices) < 20:
            raise ValueError(f"{name} daily data is insufficient")
        closes = [item["close"] for item in prices]
        last_close = closes[-1]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        prev_ma5 = sum(closes[-6:-1]) / 5 if len(closes) >= 21 else ma5
        prev_ma10 = sum(closes[-11:-1]) / 10 if len(closes) >= 21 else ma10
        return {
            "last_close": last_close,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma5_slope": ma5 - prev_ma5,
            "ma10_slope": ma10 - prev_ma10,
        }

    def _get_today_prices(self, cache_key: str, code: str, base_url: str) -> list[MinutePrice]:
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1]

        query = urllib.parse.urlencode({"since": "TODAY"})
        req = urllib.request.Request(f"{base_url.format(code=code)}?{query}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        prices: list[MinutePrice] = []
        for item in data.get("prices", []):
            ts = item.get("tm")
            if not isinstance(ts, int):
                continue
            dt = datetime.fromtimestamp(ts / 1000, BEIJING_TZ)
            minute = dt.strftime("%H:%M")
            if "09:30" <= minute <= "15:00":
                price = float(item["p"])
                prices.append(
                    MinutePrice(
                        day=dt.strftime("%Y-%m-%d"),
                        minute=minute,
                        price=price,
                        avg_price=float(item.get("a") or price),
                    )
                )

        self._cache[cache_key] = (now, prices)
        return prices

    def get_daily_ohlcs(self, name: str) -> list[dict[str, float]]:
        cache_key = f"daily:{name}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < 300:
            return cached[1]  # type: ignore[return-value]

        code = STOCKS[name]
        query = urllib.parse.urlencode({"span": "DAY1", "limit": 40})
        url = f"https://market.ft.tech/app/api/v2/stocks/{code}/ohlcs?{query}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        rows = []
        for item in data.get("ohlcs", []):
            rows.append(
                {
                    "open": float(item["o"]),
                    "high": float(item["h"]),
                    "low": float(item["l"]),
                    "close": float(item["c"]),
                }
            )
        self._cache[cache_key] = (now, rows)  # type: ignore[assignment]
        return rows


class SignalStore:
    def __init__(self, path: Path, max_daily_trades: int = 2) -> None:
        self.path = path
        self.max_daily_trades = max_daily_trades
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"signals": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record_if_new(self, signal: dict[str, Any]) -> tuple[bool, int]:
        state = self._load()
        signals = state.setdefault("signals", [])
        same_day = [
            s
            for s in signals
            if s.get("symbol") == signal["symbol"] and s.get("trade_day") == signal["trade_day"]
        ]
        if any(s.get("signal_key") == signal["signal_key"] for s in same_day):
            return False, len(same_day)
        if len(same_day) >= self.max_daily_trades:
            return False, len(same_day)
        signals.append(signal)
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, len(same_day) + 1

    def today_count(self, symbol: str, trade_day: str) -> int:
        state = self._load()
        return sum(
            1
            for s in state.get("signals", [])
            if s.get("symbol") == symbol and s.get("trade_day") == trade_day
        )


class SignalEngine:
    def __init__(self, client: MarketClient, store: SignalStore, notifier: MultiNotifier | None = None) -> None:
        self.client = client
        self.store = store
        self.notifier = notifier

    def check_all(self) -> dict[str, Any]:
        results = []
        for symbol in PARAMS:
            try:
                results.append(self.check_symbol(symbol))
            except Exception as exc:  # Keep one flaky remote request from breaking the dashboard.
                results.append({"symbol": symbol, "status": "error", "message": str(exc)})

        alerts = [item for item in results if item.get("status") == "signal" and item.get("is_new")]
        for alert in alerts:
            if self.notifier and self.notifier.enabled:
                try:
                    self.notifier.send_signal(alert)
                    alert["notify_status"] = "sent"
                except Exception as exc:
                    alert["notify_status"] = "failed"
                    alert["notify_error"] = str(exc)
        return {
            "checked_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "alerts": alerts,
            "items": results,
            "disclaimer": "分钟级辅助信号，不是投资建议；下单前请以券商行情和个人风控为准。",
        }

    def check_symbol(self, symbol: str) -> dict[str, Any]:
        own = self.client.get_today_prices(symbol)
        if len(own) < 20:
            return {"symbol": symbol, "status": "waiting", "message": "分时数据不足"}

        current = own[-1]
        window_name = self._entry_window(current.minute)
        if window_name is None:
            return self._snapshot(symbol, current, "idle", "不在入场提醒时间窗：09:45-10:30 / 14:00-14:30")

        own_open = own[0].price
        own_return = current.price / own_open - 1
        basket_stats = self._basket_stats(symbol, current.day, current.minute)
        if basket_stats is None:
            return self._snapshot(symbol, current, "waiting", "相似股篮子数据不足")
        basket_return, basket_dispersion = basket_stats

        market_return = self._market_return(current.day, current.minute)
        if market_return is None:
            return self._snapshot(symbol, current, "waiting", "大盘分时数据不足")

        trend = self.client.get_daily_trend(symbol)
        trend_score = 0
        trend_mode = "MA参考未参与"

        (
            basket_threshold,
            market_threshold,
            rel_threshold,
            avg_threshold,
            take_profit,
            stop_loss,
            max_basket_dispersion,
        ) = PARAMS[symbol]
        relative_return = own_return - basket_return
        above_avg = current.price > current.avg_price * (1 + avg_threshold)

        signal_score = self._signal_score(
            basket_return=basket_return,
            market_return=market_return,
            relative_return=relative_return,
            avg_threshold=avg_threshold,
            current=current,
            basket_dispersion=basket_dispersion,
            trend_score=trend_score,
        )

        action = None
        if (
            basket_return > basket_threshold
            and market_return > market_threshold
            and basket_dispersion <= max_basket_dispersion
            and above_avg
            and relative_return >= rel_threshold
        ):
            action = "BUY_T"

        if not action:
            return {
                **self._snapshot(symbol, current, "watching", "暂无做 T 信号"),
                "market_return_pct": round(market_return * 100, 2),
                "basket_return_pct": round(basket_return * 100, 2),
                "basket_dispersion_pct": round(basket_dispersion * 100, 2),
                "relative_return_pct": round(relative_return * 100, 2),
                "trend_mode": trend_mode,
                "trend_score": trend_score,
                "signal_score": round(signal_score, 2),
            }

        signal = self._make_signal(
            symbol=symbol,
            action=action,
            current=current,
            own_return=own_return,
            basket_return=basket_return,
            basket_dispersion=basket_dispersion,
            market_return=market_return,
            relative_return=relative_return,
            take_profit=take_profit,
            stop_loss=stop_loss,
            window_name=window_name,
            trend=trend,
            trend_mode=trend_mode,
            trend_score=trend_score,
            signal_score=signal_score,
        )
        is_new, daily_count = self.store.record_if_new(signal)
        signal["is_new"] = is_new
        signal["daily_count"] = daily_count
        if not is_new and daily_count >= self.store.max_daily_trades:
            signal["message"] = "达到每日最多 2T 限制，后续只观察不提醒"
        return signal

    def _market_return(self, day: str, minute: str) -> float | None:
        values = []
        weights = {"上证": 0.25, "深成": 0.30, "创业板": 0.45}
        for index_name, weight in weights.items():
            prices = self.client.get_today_index_prices(index_name)
            day_prices = [p for p in prices if p.day == day]
            if not day_prices:
                continue
            index_open = day_prices[0].price
            current = next((p for p in reversed(day_prices) if p.minute <= minute), None)
            if current is not None and index_open > 0:
                values.append(weight * (current.price / index_open - 1))
        if len(values) != 3:
            return None
        return sum(values)

    def _entry_window(self, minute: str) -> str | None:
        for name, start, end in ENTRY_WINDOWS:
            if start <= minute <= end:
                return name
        return None

    def _trend_score(self, symbol: str, price: float, trend: dict[str, Any]) -> tuple[int, str, str]:
        ma5 = trend["ma5"]
        ma10 = trend["ma10"]
        ma20 = trend["ma20"]
        if price < ma20 * 0.995:
            return -3, "破位禁做", f"价格低于 MA20，禁做：MA20={ma20:.2f}"

        score = 0

        if symbol == "东山精密":
            if ma5 >= ma10 >= ma20:
                score += 2
            if trend["ma10_slope"] >= 0:
                score += 1
            if price > ma5:
                score += 1
            elif ma5 * 0.985 <= price <= ma5 * 1.025:
                score += 1
            if price > ma10:
                score += 1
            if score >= 4:
                return score, "强趋势延续", "强趋势结构加分"
            if score >= 2:
                return score, "趋势中性", "趋势作为轻度加分"
            return score - 1, "趋势偏弱", f"东山趋势偏弱：MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}"

        if symbol == "剑桥科技":
            if ma20 * 0.995 <= price <= ma20 * 1.08 and price > ma20:
                score += 2
            if price > ma10:
                score += 1
            if ma5 >= ma10 * 0.995:
                score += 1
            if trend["ma5_slope"] >= 0:
                score += 1
            if score >= 3:
                return score, "MA20防守/短线修复", "趋势作为中度加分"
            if score >= 1:
                return score, "趋势中性", "趋势作为轻度加分"
            return score - 1, "趋势偏弱", f"剑桥趋势偏弱：MA10={ma10:.2f}, MA20={ma20:.2f}"

        return score, "默认", "趋势评分"

    def _signal_score(
        self,
        basket_return: float,
        market_return: float,
        relative_return: float,
        avg_threshold: float,
        current: MinutePrice,
        basket_dispersion: float,
        trend_score: int,
    ) -> float:
        score = 0.0
        score += min(3.0, max(0.0, basket_return * 100 / 0.8))
        score += min(2.0, max(0.0, market_return * 100 / 0.4))
        score += min(2.0, max(0.0, relative_return * 100 / 0.6))
        avg_excess = current.price / current.avg_price - 1 - avg_threshold
        score += min(1.5, max(0.0, avg_excess * 100 / 0.4))
        score -= min(2.0, max(0.0, (basket_dispersion - 0.03) * 100 / 1.5))
        score += max(-1.5, min(1.5, trend_score * 0.35))
        return score

    def _basket_stats(self, symbol: str, day: str, minute: str) -> tuple[float, float] | None:
        values = []
        for peer in BASKETS[symbol]:
            prices = self.client.get_today_prices(peer)
            day_prices = [p for p in prices if p.day == day]
            if not day_prices:
                continue
            peer_open = day_prices[0].price
            current = next((p for p in reversed(day_prices) if p.minute <= minute), None)
            if current is not None and peer_open > 0:
                values.append(current.price / peer_open - 1)
        if len(values) < 4:
            return None
        avg = sum(values) / len(values)
        variance = sum((value - avg) ** 2 for value in values) / len(values)
        return avg, variance ** 0.5

    def _make_signal(
        self,
        symbol: str,
        action: str,
        current: MinutePrice,
        own_return: float,
        basket_return: float,
        basket_dispersion: float,
        market_return: float,
        relative_return: float,
        take_profit: float,
        stop_loss: float,
        window_name: str,
        trend: dict[str, Any],
        trend_mode: str,
        trend_score: int,
        signal_score: float,
    ) -> dict[str, Any]:
        price = current.price
        if action == "BUY_T":
            entry_label = "建议买入T仓"
            exit_label = "目标卖出"
            entry_price = price
            exit_price = price * (1 + take_profit)
            stop_price = price * (1 - stop_loss)
        else:
            entry_label = "建议卖出T仓"
            exit_label = "目标买回"
            entry_price = price
            exit_price = price * (1 - take_profit)
            stop_price = price * (1 + stop_loss)

        signal_key = f"{symbol}:{current.day}:{current.minute}:{action}"
        return {
            "status": "signal",
            "signal_key": signal_key,
            "symbol": symbol,
            "code": STOCKS[symbol],
            "trade_day": current.day,
            "minute": current.minute,
            "window": window_name,
            "trend_mode": trend_mode,
            "trend_score": trend_score,
            "signal_score": round(signal_score, 2),
            "action": action,
            "entry_label": entry_label,
            "exit_label": exit_label,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_price": round(stop_price, 2),
            "last_price": round(price, 2),
            "avg_price": round(current.avg_price, 2),
            "own_return_pct": round(own_return * 100, 2),
            "market_return_pct": round(market_return * 100, 2),
            "basket_return_pct": round(basket_return * 100, 2),
            "basket_dispersion_pct": round(basket_dispersion * 100, 2),
            "relative_return_pct": round(relative_return * 100, 2),
            "ma5": round(trend["ma5"], 2),
            "ma10": round(trend["ma10"], 2),
            "ma20": round(trend["ma20"], 2),
            "take_profit_pct": round(take_profit * 100, 2),
            "stop_loss_pct": round(stop_loss * 100, 2),
            "message": "检测到做T信号",
        }

    def _snapshot(self, symbol: str, current: MinutePrice, status: str, message: str) -> dict[str, Any]:
        return {
            "status": status,
            "symbol": symbol,
            "code": STOCKS[symbol],
            "trade_day": current.day,
            "minute": current.minute,
            "last_price": round(current.price, 2),
            "avg_price": round(current.avg_price, 2),
            "daily_count": self.store.today_count(symbol, current.day),
            "message": message,
        }


def build_engine(data_dir: Path) -> SignalEngine:
    return SignalEngine(
        client=MarketClient(),
        store=SignalStore(data_dir / "state.json", max_daily_trades=1),
        notifier=build_notifier(),
    )
