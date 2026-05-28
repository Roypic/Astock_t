from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notifier import MultiNotifier, PushPlusNotifier


BEIJING_TZ = timezone(timedelta(hours=8))
STOCK_PRICE_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/prices"
INDEX_PRICE_URL = "https://market.ft.tech/app/api/v2/indices/{code}/prices"
STOCK_OHLC_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/ohlcs"
HEADERS = {"X-Client-Name": "ft-claw", "Content-Type": "application/json"}

INDEX_CODES = {
    "上证": "000001.XSHG",
    "深成": "399001.XSHE",
    "创业板": "399006.XSHE",
}

DEFAULT_ENTRY_WINDOWS = (
    ("盘中", "09:30", "15:00"),
)


@dataclass(frozen=True)
class Security:
    name: str
    code: str


@dataclass(frozen=True)
class TModel:
    name: str
    code: str
    basket: tuple[Security, ...]
    basket_threshold: float
    market_threshold: float
    relative_threshold: float
    avg_threshold: float
    take_profit: float
    stop_loss: float
    max_basket_dispersion: float
    volume_ratio_threshold: float = 0.0
    max_daily_signals: int = 1
    trade_sides: str = "buy"


@dataclass
class MinutePrice:
    day: str
    minute: str
    price: float
    avg_price: float
    volume: float = 0.0
    amount: float = 0.0


class MarketClient:
    def __init__(self, ttl_seconds: int = 20) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    def get_stock_prices(self, code: str) -> list[MinutePrice]:
        return self._get_prices(f"stock:{code}", STOCK_PRICE_URL, code)

    def get_index_prices(self, code: str) -> list[MinutePrice]:
        return self._get_prices(f"index:{code}", INDEX_PRICE_URL, code)

    def get_daily_trend(self, code: str) -> dict[str, float | None]:
        cache_key = f"daily:{code}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < 300:
            return cached[1]

        query = urllib.parse.urlencode({"span": "DAY1", "limit": 40})
        req = urllib.request.Request(f"{STOCK_OHLC_URL.format(code=code)}?{query}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        closes = [float(item["c"]) for item in data.get("ohlcs", []) if "c" in item]
        if len(closes) < 20:
            trend = {"ma5": None, "ma10": None, "ma20": None}
        else:
            trend = {
                "ma5": sum(closes[-5:]) / 5,
                "ma10": sum(closes[-10:]) / 10,
                "ma20": sum(closes[-20:]) / 20,
            }
        self._cache[cache_key] = (now, trend)
        return trend

    def _get_prices(self, cache_key: str, base_url: str, code: str) -> list[MinutePrice]:
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
                        volume=float(item.get("v") or 0),
                        amount=float(item.get("t") or 0),
                    )
                )
        self._cache[cache_key] = (now, prices)
        return prices


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"signals": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record_if_new(self, signal: dict[str, Any], max_daily_signals: int) -> tuple[bool, int]:
        state = self._load()
        signals = state.setdefault("signals", [])
        same_day = [
            s
            for s in signals
            if s.get("code") == signal["code"] and s.get("trade_day") == signal["trade_day"]
        ]
        if any(s.get("signal_key") == signal["signal_key"] for s in same_day):
            return False, len(same_day)
        if len(same_day) >= max_daily_signals:
            return False, len(same_day)
        signals.append(signal)
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, len(same_day) + 1

    def today_count(self, code: str, trade_day: str) -> int:
        state = self._load()
        return sum(
            1
            for s in state.get("signals", [])
            if s.get("code") == code and s.get("trade_day") == trade_day
        )


class ModelSignalEngine:
    def __init__(
        self,
        models: list[TModel],
        data_dir: Path,
        token: str,
        entry_windows: tuple[tuple[str, str, str], ...] = DEFAULT_ENTRY_WINDOWS,
    ) -> None:
        self.models = models
        self.client = MarketClient()
        self.store = SignalStore(data_dir / "state.json")
        self.notifier = MultiNotifier([PushPlusNotifier(token)])
        self.entry_windows = entry_windows

    def check_all(self) -> dict[str, Any]:
        items = []
        alerts = []
        for model in self.models:
            try:
                item = self.check_model(model)
            except Exception as exc:
                item = {"symbol": model.name, "code": model.code, "status": "error", "message": str(exc)}
            items.append(item)
            if item.get("status") == "signal" and item.get("is_new"):
                try:
                    self.notifier.send_signal(item)
                    item["notify_status"] = "sent"
                except Exception as exc:
                    item["notify_status"] = "failed"
                    item["notify_error"] = str(exc)
                alerts.append(item)
        return {
            "checked_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "items": items,
            "alerts": alerts,
        }

    def check_model(self, model: TModel) -> dict[str, Any]:
        own = self.client.get_stock_prices(model.code)
        if len(own) < 20:
            return self._snapshot(model, None, "waiting", "分时数据不足")

        current = own[-1]
        window_name = self._entry_window(current.minute)
        if window_name is None:
            return self._snapshot(model, current, "idle", "不在入场提醒时间窗")

        own_open = own[0].price
        own_return = current.price / own_open - 1
        basket_stats = self._basket_stats(model, current.day, current.minute)
        if basket_stats is None:
            return self._snapshot(model, current, "waiting", "相似股篮子数据不足")
        basket_return, basket_dispersion = basket_stats

        market_return = self._market_return(current.day, current.minute)
        if market_return is None:
            return self._snapshot(model, current, "waiting", "大盘分时数据不足")

        relative_return = own_return - basket_return
        above_avg = current.price > current.avg_price * (1 + model.avg_threshold)
        below_avg = current.price < current.avg_price * (1 - model.avg_threshold)
        volume_ratio = self._volume_ratio(own, len(own) - 1)
        buy_score = self._score(model, current, basket_return, market_return, relative_return, basket_dispersion, "BUY_T")
        sell_score = self._score(model, current, basket_return, market_return, relative_return, basket_dispersion, "SELL_T")

        has_buy_signal = (
            model.trade_sides in ("both", "buy")
            and basket_return > model.basket_threshold
            and market_return > model.market_threshold
            and relative_return >= model.relative_threshold
            and basket_dispersion <= model.max_basket_dispersion
            and above_avg
            and volume_ratio >= model.volume_ratio_threshold
        )
        has_sell_signal = (
            model.trade_sides in ("both", "sell")
            and basket_return < -model.basket_threshold
            and market_return < -model.market_threshold
            and relative_return <= -model.relative_threshold
            and basket_dispersion <= model.max_basket_dispersion
            and below_avg
            and volume_ratio >= model.volume_ratio_threshold
        )
        action = "BUY_T" if has_buy_signal else "SELL_T" if has_sell_signal else ""
        score = buy_score if action == "BUY_T" else sell_score

        if not action:
            return {
                **self._snapshot(model, current, "watching", "暂无做T信号"),
                "own_return_pct": round(own_return * 100, 2),
                "market_return_pct": round(market_return * 100, 2),
                "basket_return_pct": round(basket_return * 100, 2),
                "basket_dispersion_pct": round(basket_dispersion * 100, 2),
                "relative_return_pct": round(relative_return * 100, 2),
                "signal_score": round(max(buy_score, sell_score), 2),
                "volume_ratio": round(volume_ratio, 2),
            }

        trend = self.client.get_daily_trend(model.code)
        signal = self._make_signal(
            model=model,
            current=current,
            own_return=own_return,
            basket_return=basket_return,
            basket_dispersion=basket_dispersion,
            market_return=market_return,
            relative_return=relative_return,
            score=score,
            window_name=window_name,
            trend=trend,
            volume_ratio=volume_ratio,
            action=action,
        )
        is_new, count = self.store.record_if_new(signal, model.max_daily_signals)
        signal["is_new"] = is_new
        signal["daily_count"] = count
        return signal

    def _entry_window(self, minute: str) -> str | None:
        for name, start, end in self.entry_windows:
            if start <= minute <= end:
                return name
        return None

    def _basket_stats(self, model: TModel, day: str, minute: str) -> tuple[float, float] | None:
        values = []
        for peer in model.basket:
            prices = self.client.get_stock_prices(peer.code)
            day_prices = [p for p in prices if p.day == day]
            if not day_prices:
                continue
            current = next((p for p in reversed(day_prices) if p.minute <= minute), None)
            if current and day_prices[0].price > 0:
                values.append(current.price / day_prices[0].price - 1)
        if len(values) < max(3, min(4, len(model.basket))):
            return None
        avg = sum(values) / len(values)
        variance = sum((value - avg) ** 2 for value in values) / len(values)
        return avg, variance ** 0.5

    def _market_return(self, day: str, minute: str) -> float | None:
        values = []
        weights = {"上证": 0.25, "深成": 0.30, "创业板": 0.45}
        for name, weight in weights.items():
            prices = self.client.get_index_prices(INDEX_CODES[name])
            day_prices = [p for p in prices if p.day == day]
            if not day_prices:
                continue
            current = next((p for p in reversed(day_prices) if p.minute <= minute), None)
            if current and day_prices[0].price > 0:
                values.append(weight * (current.price / day_prices[0].price - 1))
        if len(values) != 3:
            return None
        return sum(values)

    def _score(
        self,
        model: TModel,
        current: MinutePrice,
        basket_return: float,
        market_return: float,
        relative_return: float,
        basket_dispersion: float,
        action: str,
    ) -> float:
        direction = 1.0 if action == "BUY_T" else -1.0
        score = 0.0
        score += min(3.0, max(0.0, direction * basket_return * 100 / 0.8))
        score += min(2.0, max(0.0, direction * market_return * 100 / 0.4))
        score += min(2.0, max(0.0, direction * relative_return * 100 / 0.6))
        avg_excess = direction * (current.price / current.avg_price - 1) - model.avg_threshold
        score += min(1.5, max(0.0, avg_excess * 100 / 0.4))
        score -= min(2.0, max(0.0, (basket_dispersion - 0.03) * 100 / 1.5))
        return score

    def _volume_ratio(self, rows: list[MinutePrice], index: int) -> float:
        if index < 10:
            return 1.0
        recent = rows[max(0, index - 4) : index + 1]
        prior = rows[: max(1, index - 4)]
        recent_avg = sum(item.volume for item in recent) / max(1, len(recent))
        prior_avg = sum(item.volume for item in prior) / max(1, len(prior))
        if prior_avg <= 0:
            return 1.0
        return recent_avg / prior_avg

    def _make_signal(
        self,
        model: TModel,
        current: MinutePrice,
        own_return: float,
        basket_return: float,
        basket_dispersion: float,
        market_return: float,
        relative_return: float,
        score: float,
        window_name: str,
        trend: dict[str, float | None],
        volume_ratio: float,
        action: str,
    ) -> dict[str, Any]:
        entry_price = current.price
        if action == "SELL_T":
            exit_price = current.price * (1 - model.take_profit)
            stop_price = current.price * (1 + model.stop_loss)
            entry_label = "建议卖出T仓"
            exit_label = "目标买回"
        else:
            exit_price = current.price * (1 + model.take_profit)
            stop_price = current.price * (1 - model.stop_loss)
            entry_label = "建议买入T仓"
            exit_label = "目标卖出"
        return {
            "status": "signal",
            "signal_key": f"{model.code}:{current.day}:{current.minute}:{action}",
            "symbol": model.name,
            "code": model.code,
            "trade_day": current.day,
            "minute": current.minute,
            "window": window_name,
            "trend_mode": "MA参考未参与",
            "trend_score": 0,
            "signal_score": round(score, 2),
            "action": action,
            "entry_label": entry_label,
            "exit_label": exit_label,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_price": round(stop_price, 2),
            "last_price": round(current.price, 2),
            "avg_price": round(current.avg_price, 2),
            "own_return_pct": round(own_return * 100, 2),
            "market_return_pct": round(market_return * 100, 2),
            "basket_return_pct": round(basket_return * 100, 2),
            "basket_dispersion_pct": round(basket_dispersion * 100, 2),
            "relative_return_pct": round(relative_return * 100, 2),
            "volume_ratio": round(volume_ratio, 2),
            "ma5": _round_or_dash(trend.get("ma5")),
            "ma10": _round_or_dash(trend.get("ma10")),
            "ma20": _round_or_dash(trend.get("ma20")),
            "take_profit_pct": round(model.take_profit * 100, 2),
            "stop_loss_pct": round(model.stop_loss * 100, 2),
            "max_daily_signals": model.max_daily_signals,
            "message": "检测到做T信号",
        }

    def _snapshot(
        self,
        model: TModel,
        current: MinutePrice | None,
        status: str,
        message: str,
    ) -> dict[str, Any]:
        day = current.day if current else "-"
        return {
            "status": status,
            "symbol": model.name,
            "code": model.code,
            "trade_day": day,
            "minute": current.minute if current else "-",
            "last_price": round(current.price, 2) if current else "-",
            "avg_price": round(current.avg_price, 2) if current else "-",
            "daily_count": self.store.today_count(model.code, day) if current else 0,
            "message": message,
        }


def load_models(path: Path) -> list[TModel]:
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    models = [_load_model(file) for file in files]
    if not models:
        raise ValueError("没有找到模型 JSON 文件")
    return models


def _load_model(path: Path) -> TModel:
    data = json.loads(path.read_text(encoding="utf-8"))
    basket = tuple(Security(str(item["name"]), str(item["code"])) for item in data.get("basket", []))
    if not basket:
        raise ValueError(f"{path.name} 缺少 basket")
    params = data.get("params", {})
    return TModel(
        name=str(data["name"]),
        code=str(data["code"]),
        basket=basket,
        basket_threshold=float(params["basket_threshold"]),
        market_threshold=float(params["market_threshold"]),
        relative_threshold=float(params["relative_threshold"]),
        avg_threshold=float(params["avg_threshold"]),
        take_profit=float(params["take_profit"]),
        stop_loss=float(params["stop_loss"]),
        max_basket_dispersion=float(params["max_basket_dispersion"]),
        volume_ratio_threshold=float(params.get("volume_ratio_threshold", 0.0)),
        max_daily_signals=int(params.get("max_daily_signals", 1)),
        trade_sides=str(params.get("trade_sides", "buy")),
    )


def _round_or_dash(value: float | None) -> float | str:
    return round(value, 2) if isinstance(value, (int, float)) else "-"
