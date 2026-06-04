from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notifier import MultiNotifier, PushPlusNotifier, WeixinPushNotifier
from net_utils import safe_urlopen


BEIJING_TZ = timezone(timedelta(hours=8))
STOCK_PRICE_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/prices"
ETF_PRICE_URL = "https://market.ft.tech/app/api/v2/etfs/{code}/prices"
INDEX_PRICE_URL = "https://market.ft.tech/app/api/v2/indices/{code}/prices"
STOCK_OHLC_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/ohlcs"
ETF_OHLC_URL = "https://market.ft.tech/app/api/v2/etfs/{code}/ohlcs"
HEADERS = {"X-Client-Name": "ft-claw", "Content-Type": "application/json"}

INDEX_CODES = {
    "上证": "000001.XSHG",
    "深成": "399001.XSHE",
    "创业板": "399006.XSHE",
}

DEFAULT_ENTRY_WINDOWS = (
    ("盘中", "09:30", "15:00"),
)

TRADING_SESSIONS = (
    ("09:30", "11:30"),
    ("13:00", "15:00"),
)


def is_etf_code(code: str) -> bool:
    code6 = str(code or "").upper().split(".", 1)[0]
    return code6.startswith(("15", "16", "18", "51", "52", "56", "58"))


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
    strategy_mode: str = "intraday_aggressive"
    min_signal_gap_minutes: int = 20
    min_signal_score: float = 1.8


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
        if is_etf_code(code):
            return self._get_prices(f"etf:{code}", ETF_PRICE_URL, code)
        return self._get_prices(f"stock:{code}", STOCK_PRICE_URL, code)

    def get_index_prices(self, code: str) -> list[MinutePrice]:
        return self._get_prices(f"index:{code}", INDEX_PRICE_URL, code)

    def get_daily_trend(self, code: str) -> dict[str, float | None]:
        cache_key = f"daily:{'etf' if is_etf_code(code) else 'stock'}:{code}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < 300:
            return cached[1]

        query = urllib.parse.urlencode({"span": "DAY1", "limit": 40})
        base_url = ETF_OHLC_URL if is_etf_code(code) else STOCK_OHLC_URL
        req = urllib.request.Request(f"{base_url.format(code=code)}?{query}", headers=HEADERS)
        with safe_urlopen(req, timeout=12) as resp:
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
        with safe_urlopen(req, timeout=12) as resp:
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

    def record_if_new(self, signal: dict[str, Any], max_daily_signals: int, min_gap_minutes: int = 0) -> tuple[bool, int]:
        state = self._load()
        signals = state.setdefault("signals", [])
        same_day = [
            s
            for s in signals
            if s.get("code") == signal["code"] and s.get("trade_day") == signal["trade_day"]
        ]
        if any(s.get("signal_key") == signal["signal_key"] for s in same_day):
            return False, len(same_day)
        action = signal.get("action")
        signal_minute = _minute_to_int(str(signal.get("minute", "")))
        if min_gap_minutes > 0 and signal_minute is not None:
            for previous in reversed(same_day):
                if previous.get("action") != action:
                    continue
                previous_minute = _minute_to_int(str(previous.get("minute", "")))
                if previous_minute is not None and signal_minute - previous_minute < min_gap_minutes:
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

    def signals_for_day(self, trade_day: str, codes: set[str] | None = None) -> list[dict[str, Any]]:
        state = self._load()
        rows = []
        for signal in state.get("signals", []):
            if signal.get("trade_day") != trade_day:
                continue
            if codes is not None and signal.get("code") not in codes:
                continue
            rows.append(signal)
        return sorted(rows, key=lambda item: (str(item.get("code", "")), str(item.get("minute", ""))))


class ModelSignalEngine:
    def __init__(
        self,
        models: list[TModel],
        data_dir: Path,
        token: str,
        weixin_mode: str = "",
        entry_windows: tuple[tuple[str, str, str], ...] = DEFAULT_ENTRY_WINDOWS,
    ) -> None:
        self.models = models
        self.client = MarketClient()
        self.store = SignalStore(data_dir / "state.json")
        self.notifier = MultiNotifier([PushPlusNotifier(token), WeixinPushNotifier(weixin_mode)])
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

    def build_daily_battle_report(self, trade_day: str | None = None) -> dict[str, Any]:
        day = trade_day or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        model_codes = {model.code for model in self.models}
        signals = self.store.signals_for_day(day, model_codes)
        now = datetime.now(BEIJING_TZ)
        lines = [
            f"做T盘后战报（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
            f"交易日：{day}",
            "口径：只回看已推送信号之后，当日分时是否触达目标2/参考风控；不代表真实成交、滑点或手续费。",
            "",
        ]
        if not signals:
            lines.append("今日自选模型没有记录到做T信号。")
            return {
                "trade_day": day,
                "total": 0,
                "success": 0,
                "success_rate_pct": 0.0,
                "content": "\n".join(lines),
                "summary": f"{day} 无做T信号",
            }

        evaluations = [self._evaluate_signal(signal) for signal in signals]
        success = sum(1 for item in evaluations if item["success"])
        stopped = sum(1 for item in evaluations if item["stopped"])
        pending = sum(1 for item in evaluations if item["outcome"] == "数据不足")
        total = len(evaluations)
        valid = total - pending
        avg_result = (
            sum(float(item["result_pct"]) for item in evaluations if item["outcome"] != "数据不足") / valid
            if valid > 0
            else 0.0
        )
        success_rate = success / valid * 100 if valid > 0 else 0.0
        lines.extend(
            [
                f"总信号：{total} 个；有效回看：{valid} 个；目标2触达：{success} 个；先触风控：{stopped} 个。",
                f"粗略成功率：{success_rate:.2f}%；粗略单次均值：{avg_result:.2f}%。",
                "",
                "逐笔回看：",
            ]
        )
        for item in evaluations:
            lines.append(
                f"- {item['symbol']} {item['minute']} {item['side']}：入场 {item['entry_price']}，"
                f"目标2 {item['target_price']}，风控 {item['stop_price']}，收盘 {item['close_price']}，"
                f"结果 {item['outcome']}，估算 {item['result_pct']:.2f}%"
            )
        lines.extend(
            [
                "",
                "提醒：这是战后复盘，不是收益确认；如果你没有按信号成交、目标价没挂到、或中途手动处理，实际结果会不同。",
            ]
        )
        return {
            "trade_day": day,
            "total": total,
            "success": success,
            "success_rate_pct": round(success_rate, 2),
            "avg_result_pct": round(avg_result, 2),
            "content": "\n".join(lines),
            "summary": f"{day} 信号{total} 成功率{success_rate:.2f}% 均值{avg_result:.2f}%",
        }

    def _evaluate_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        code = str(signal.get("code") or "")
        day = str(signal.get("trade_day") or "")
        minute = str(signal.get("minute") or "")
        action = str(signal.get("action") or "")
        entry = _float_or_none(signal.get("entry_price"))
        target = _float_or_none(signal.get("exit_price") or signal.get("target2_price"))
        stop = _float_or_none(signal.get("stop_price"))
        base = {
            "symbol": signal.get("symbol", code),
            "minute": minute,
            "side": "正T" if action == "BUY_T" else "倒T" if action == "SELL_T" else action,
            "entry_price": entry or "-",
            "target_price": target or "-",
            "stop_price": stop or "-",
            "close_price": "-",
            "success": False,
            "stopped": False,
            "outcome": "数据不足",
            "result_pct": 0.0,
        }
        if not code or not day or not minute or entry is None or target is None or stop is None:
            return base
        prices = [row for row in self.client.get_stock_prices(code) if row.day == day and row.minute >= minute]
        if not prices:
            return base
        close = prices[-1].price
        base["close_price"] = round(close, 3)
        target_hit_minute = None
        stop_hit_minute = None
        for row in prices:
            if action == "SELL_T":
                if target_hit_minute is None and row.price <= target:
                    target_hit_minute = row.minute
                if stop_hit_minute is None and row.price >= stop:
                    stop_hit_minute = row.minute
            else:
                if target_hit_minute is None and row.price >= target:
                    target_hit_minute = row.minute
                if stop_hit_minute is None and row.price <= stop:
                    stop_hit_minute = row.minute
            if target_hit_minute and stop_hit_minute:
                break
        if target_hit_minute and (not stop_hit_minute or target_hit_minute <= stop_hit_minute):
            base.update({"success": True, "outcome": f"目标2触达 {target_hit_minute}"})
            base["result_pct"] = abs(target / entry - 1) * 100
        elif stop_hit_minute:
            base.update({"stopped": True, "outcome": f"风控先触 {stop_hit_minute}"})
            base["result_pct"] = -abs(stop / entry - 1) * 100
        else:
            if action == "SELL_T":
                result = entry / close - 1 if close > 0 else 0.0
            else:
                result = close / entry - 1
            base.update({"outcome": "未触目标/风控，按收盘估算", "result_pct": result * 100})
        return base

    def check_model(self, model: TModel) -> dict[str, Any]:
        own = self.client.get_stock_prices(model.code)
        if len(own) < 20:
            return self._snapshot(model, None, "waiting", "分时数据不足")

        current = own[-1]
        if not self._is_live_trading_time(current.day):
            return self._snapshot(model, current, "idle", "休市或收盘后，仅显示最后行情，不推送信号")

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

        if model.strategy_mode == "intraday_aggressive":
            has_buy_signal = (
                model.trade_sides in ("both", "buy")
                and buy_score >= model.min_signal_score
                and basket_dispersion <= model.max_basket_dispersion
                and volume_ratio >= model.volume_ratio_threshold
            )
            has_sell_signal = (
                model.trade_sides in ("both", "sell")
                and sell_score >= model.min_signal_score
                and basket_dispersion <= model.max_basket_dispersion
                and volume_ratio >= model.volume_ratio_threshold
            )
        else:
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
        is_new, count = self.store.record_if_new(signal, model.max_daily_signals, model.min_signal_gap_minutes)
        signal["is_new"] = is_new
        signal["daily_count"] = count
        return signal

    def _entry_window(self, minute: str) -> str | None:
        for name, start, end in self.entry_windows:
            if start <= minute <= end:
                return name
        return None

    def _is_live_trading_time(self, data_day: str) -> bool:
        now = datetime.now(BEIJING_TZ)
        if now.strftime("%Y-%m-%d") != data_day:
            return False
        if now.weekday() >= 5:
            return False
        minute = now.strftime("%H:%M")
        return any(start <= minute <= end for start, end in TRADING_SESSIONS)

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
        score += min(1.0, max(0.0, (abs(current.price / current.avg_price - 1) - model.avg_threshold) * 100 / 0.5))
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
        target1_pct = model.take_profit * 0.5
        target2_pct = model.take_profit
        target3_pct = model.take_profit * 1.5
        if action == "SELL_T":
            exit_price = current.price * (1 - model.take_profit)
            stop_price = current.price * (1 + model.stop_loss)
            entry_label = "建议卖出T仓"
            exit_label = "目标买回"
            target1 = current.price * (1 - target1_pct)
            target2 = current.price * (1 - target2_pct)
            target3 = current.price * (1 - target3_pct)
            signal_detail = "激进倒T：先卖出已有T仓，分批在目标价买回；只捕捉日内价差。"
        else:
            exit_price = current.price * (1 + model.take_profit)
            stop_price = current.price * (1 - model.stop_loss)
            entry_label = "建议买入T仓"
            exit_label = "目标卖出"
            target1 = current.price * (1 + target1_pct)
            target2 = current.price * (1 + target2_pct)
            target3 = current.price * (1 + target3_pct)
            signal_detail = "激进正T：先买入T仓，分批在目标价卖出；只捕捉日内价差。"
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
            "signal_detail": signal_detail,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_price": round(stop_price, 2),
            "target1_price": round(target1, 2),
            "target2_price": round(target2, 2),
            "target3_price": round(target3, 2),
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
            "strategy_mode": model.strategy_mode,
            "min_signal_gap_minutes": model.min_signal_gap_minutes,
            "message": (
                f"{signal_detail} 入场价 {round(entry_price, 2)}，"
                f"目标1/2/3：{round(target1, 2)}/{round(target2, 2)}/{round(target3, 2)}，"
                f"参考风控价 {round(stop_price, 2)}。"
            ),
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
        strategy_mode=str(params.get("strategy_mode", "intraday_aggressive")),
        min_signal_gap_minutes=int(params.get("min_signal_gap_minutes", 20)),
        min_signal_score=float(params.get("min_signal_score", 1.8)),
    )


def _round_or_dash(value: float | None) -> float | str:
    return round(value, 2) if isinstance(value, (int, float)) else "-"


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _minute_to_int(minute: str) -> int | None:
    try:
        hour, value = minute.split(":", 1)
        return int(hour) * 60 + int(value)
    except Exception:
        return None
