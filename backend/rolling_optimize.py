from __future__ import annotations

import argparse
import json
import math
import statistics
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from pathlib import Path
from typing import Any

from model_engine import HEADERS, INDEX_CODES, TModel, load_models


BEIJING_TZ = timezone(timedelta(hours=8))
STOCK_PRICE_URL = "https://market.ft.tech/app/api/v2/stocks/{code}/prices"
INDEX_PRICE_URL = "https://market.ft.tech/app/api/v2/indices/{code}/prices"


def fetch_prices(code: str, is_index: bool = False, since: str = "TRADE_DAYS_AGO(31)") -> list[dict[str, Any]]:
    base = INDEX_PRICE_URL if is_index else STOCK_PRICE_URL
    query = urllib.parse.urlencode({"since": since})
    req = urllib.request.Request(f"{base.format(code=code)}?{query}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    rows = []
    for item in data.get("prices", []):
        ts = item.get("tm")
        if not isinstance(ts, int):
            continue
        dt = datetime.fromtimestamp(ts / 1000, BEIJING_TZ)
        minute = dt.strftime("%H:%M")
        if "09:30" <= minute <= "15:00":
            price = float(item["p"])
            rows.append(
                {
                    "day": dt.strftime("%Y-%m-%d"),
                    "minute": minute,
                    "price": price,
                    "avg_price": float(item.get("a") or price),
                    "volume": float(item.get("v") or 0),
                    "amount": float(item.get("t") or 0),
                }
            )
    return rows


def group_by_day(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["day"], []).append(row)
    return grouped


def latest_common_days(price_map: dict[str, dict[str, list[dict[str, Any]]]]) -> list[str]:
    sets = [set(days.keys()) for days in price_map.values()]
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def nearest_at(rows: list[dict[str, Any]], minute: str) -> dict[str, Any] | None:
    found = None
    for row in rows:
        if row["minute"] <= minute:
            found = row
        else:
            break
    return found


def basket_stats(
    model: TModel,
    price_map: dict[str, dict[str, list[dict[str, Any]]]],
    day: str,
    minute: str,
) -> tuple[float, float] | None:
    values = []
    for peer in model.basket:
        rows = price_map.get(peer.code, {}).get(day, [])
        if not rows:
            continue
        current = nearest_at(rows, minute)
        if current and rows[0]["price"] > 0:
            values.append(current["price"] / rows[0]["price"] - 1)
    if len(values) < max(3, min(4, len(model.basket))):
        return None
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return avg, variance ** 0.5


def market_return(price_map: dict[str, dict[str, list[dict[str, Any]]]], day: str, minute: str) -> float | None:
    weights = {"000001.XSHG": 0.25, "399001.XSHE": 0.30, "399006.XSHE": 0.45}
    values = []
    for code, weight in weights.items():
        rows = price_map.get(code, {}).get(day, [])
        if not rows:
            continue
        current = nearest_at(rows, minute)
        if current and rows[0]["price"] > 0:
            values.append(weight * (current["price"] / rows[0]["price"] - 1))
    if len(values) != 3:
        return None
    return sum(values)


def volume_ratio(rows: list[dict[str, Any]], index: int) -> float:
    if index < 10:
        return 1.0
    recent = rows[max(0, index - 4) : index + 1]
    prior = rows[: max(1, index - 4)]
    recent_avg = sum(float(item.get("volume", 0)) for item in recent) / max(1, len(recent))
    prior_avg = sum(float(item.get("volume", 0)) for item in prior) / max(1, len(prior))
    if prior_avg <= 0:
        return 1.0
    return recent_avg / prior_avg


def simulate_day(
    model: TModel,
    params: dict[str, float],
    price_map: dict[str, dict[str, list[dict[str, Any]]]],
    day: str,
) -> dict[str, Any] | None:
    rows = price_map.get(model.code, {}).get(day, [])
    if len(rows) < 20:
        return None
    open_price = rows[0]["price"]
    for i, current in enumerate(rows[:-1]):
        minute = current["minute"]
        own_return = current["price"] / open_price - 1
        bstats = basket_stats(model, price_map, day, minute)
        mret = market_return(price_map, day, minute)
        if bstats is None or mret is None:
            continue
        bret, bdisp = bstats
        rel = own_return - bret
        above_avg = current["price"] > current["avg_price"] * (1 + params["avg_threshold"])
        below_avg = current["price"] < current["avg_price"] * (1 - params["avg_threshold"])
        vr = volume_ratio(rows, i)
        sides = str(params.get("trade_sides", getattr(model, "trade_sides", "buy")))
        buy_signal = (
            sides in ("both", "buy")
            and bret > params["basket_threshold"]
            and mret > params["market_threshold"]
            and rel >= params["relative_threshold"]
            and bdisp <= params["max_basket_dispersion"]
            and above_avg
            and vr >= params.get("volume_ratio_threshold", 0.0)
        )
        sell_signal = (
            sides in ("both", "sell")
            and bret < -params["basket_threshold"]
            and mret < -params["market_threshold"]
            and rel <= -params["relative_threshold"]
            and bdisp <= params["max_basket_dispersion"]
            and below_avg
            and vr >= params.get("volume_ratio_threshold", 0.0)
        )
        if not (buy_signal or sell_signal):
            continue

        action = "BUY_T" if buy_signal else "SELL_T"
        entry = current["price"]
        if action == "SELL_T":
            target = entry * (1 - params["take_profit"])
            stop = entry * (1 + params["stop_loss"])
        else:
            target = entry * (1 + params["take_profit"])
            stop = entry * (1 - params["stop_loss"])
        exit_kind = "close"
        exit_price = rows[-1]["price"]
        exit_minute = rows[-1]["minute"]
        for future in rows[i + 1 :]:
            if action == "BUY_T" and future["price"] >= target:
                exit_kind = "target"
                exit_price = target
                exit_minute = future["minute"]
                break
            if action == "BUY_T" and future["price"] <= stop:
                exit_kind = "stop"
                exit_price = stop
                exit_minute = future["minute"]
                break
            if action == "SELL_T" and future["price"] <= target:
                exit_kind = "target"
                exit_price = target
                exit_minute = future["minute"]
                break
            if action == "SELL_T" and future["price"] >= stop:
                exit_kind = "stop"
                exit_price = stop
                exit_minute = future["minute"]
                break
        result = exit_price / entry - 1 if action == "BUY_T" else entry / exit_price - 1
        return {
            "day": day,
            "minute": minute,
            "action": action,
            "entry": entry,
            "exit_minute": exit_minute,
            "exit_kind": exit_kind,
            "result": result,
            "residual": result if exit_kind == "close" else 0.0,
            "basket_return": bret,
            "market_return": mret,
            "relative_return": rel,
            "basket_dispersion": bdisp,
            "volume_ratio": vr,
        }
    return None


def metrics(trades: list[dict[str, Any]]) -> dict[str, float | int]:
    if not trades:
        return {
            "n": 0,
            "win_rate": 0.0,
            "avg_result": 0.0,
            "sum_result": 0.0,
            "max_drawdown": 0.0,
            "neg_residual": 0.0,
            "bad_tail_rate": 0.0,
            "target_rate": 0.0,
            "buy_count": 0,
            "sell_count": 0,
        }
    results = [t["result"] for t in trades]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in results:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    residuals = [t["residual"] for t in trades if t["residual"] < 0]
    return {
        "n": len(trades),
        "win_rate": sum(1 for r in results if r > 0) / len(results),
        "avg_result": statistics.mean(results),
        "sum_result": sum(results),
        "max_drawdown": max_dd,
        "neg_residual": statistics.mean(residuals) if residuals else 0.0,
        "bad_tail_rate": sum(1 for r in results if r <= -0.01) / len(results),
        "target_rate": sum(1 for t in trades if t["exit_kind"] == "target") / len(trades),
        "buy_count": sum(1 for t in trades if t.get("action") == "BUY_T"),
        "sell_count": sum(1 for t in trades if t.get("action") == "SELL_T"),
    }


def score_metrics(m: dict[str, float | int]) -> float:
    n = int(m["n"])
    if n < 5:
        return -999.0 + n
    return (
        float(m["avg_result"]) * 100
        + float(m["sum_result"]) * 9
        + float(m["win_rate"]) * 0.7
        + float(m["target_rate"]) * 0.4
        + float(m["max_drawdown"]) * 4
        + float(m["neg_residual"]) * 8
        - float(m["bad_tail_rate"]) * 0.8
        - max(0, n - 16) * 0.03
    )


def param_grid(base: TModel) -> list[dict[str, float]]:
    baskets = sorted(set(round(max(0.0, base.basket_threshold + d), 5) for d in (-0.004, 0.0, 0.004)))
    markets = sorted(set(round(base.market_threshold + d, 5) for d in (0.0, 0.003)))
    relatives = sorted(set(round(base.relative_threshold + d, 5) for d in (0.0, 0.003)))
    avgs = sorted(set(round(max(0.0, base.avg_threshold + d), 5) for d in (0.0, 0.002)))
    tps = sorted(set([base.take_profit, 0.020]))
    stops = sorted(set([base.stop_loss, 0.012]))
    dispersions = sorted(set([base.max_basket_dispersion, 0.045]))
    volume_thresholds = sorted(set([0.0, base.volume_ratio_threshold, 1.1, 1.3]))
    trade_sides = sorted(set([base.trade_sides, "both", "sell"]))
    grid = []
    for basket, market, rel, avg, tp, stop, disp, vol, sides in product(
        baskets, markets, relatives, avgs, tps, stops, dispersions, volume_thresholds, trade_sides
    ):
        if tp < 0.016:
            continue
        grid.append(
            {
                "basket_threshold": round(basket, 5),
                "market_threshold": round(market, 5),
                "relative_threshold": round(rel, 5),
                "avg_threshold": round(avg, 5),
                "take_profit": round(tp, 5),
                "stop_loss": round(stop, 5),
                "max_basket_dispersion": round(disp, 5),
                "volume_ratio_threshold": round(vol, 5),
                "trade_sides": sides,
            }
        )
    return grid


def optimize_model(
    model: TModel,
    price_map: dict[str, dict[str, list[dict[str, Any]]]],
    train_days: list[str],
    test_days: list[str],
) -> dict[str, Any]:
    best = None
    best_trades: list[dict[str, Any]] = []
    for params in param_grid(model):
        trades = [trade for day in train_days if (trade := simulate_day(model, params, price_map, day))]
        m = metrics(trades)
        s = score_metrics(m)
        if best is None or s > best["score"]:
            best = {"score": s, "params": params, "train_metrics": m}
            best_trades = trades
    assert best is not None
    test_trades = [trade for day in test_days if (trade := simulate_day(model, best["params"], price_map, day))]
    best["test_metrics"] = metrics(test_trades)
    best["train_trades"] = best_trades
    best["test_trades"] = test_trades
    return best


def cached_fetch(code: str, is_index: bool, since: str, cache_dir: Path) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    kind = "index" if is_index else "stock"
    safe_since = since.replace("(", "_").replace(")", "").replace("/", "_")
    cache_file = cache_dir / f"v2_{kind}_{code}_{safe_since}.json"
    if cache_file.exists():
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        print(f"fetch {kind} {code}", flush=True)
        rows = fetch_prices(code, is_index=is_index, since=since)
        cache_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return code, group_by_day(rows)


def collect_data(models: list[TModel], since: str, cache_dir: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    codes = {model.code for model in models}
    for model in models:
        codes.update(peer.code for peer in model.basket)
    index_codes = set(INDEX_CODES.values())
    price_map = {}
    jobs = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for code in sorted(codes):
            jobs.append(pool.submit(cached_fetch, code, False, since, cache_dir))
        for code in sorted(index_codes):
            jobs.append(pool.submit(cached_fetch, code, True, since, cache_dir))
        for future in as_completed(jobs):
            code, grouped = future.result()
            price_map[code] = grouped
            print(f"loaded {code}: {len(grouped)} days", flush=True)
    return price_map


def pct(value: float | int) -> float:
    return round(float(value) * 100, 2)


def summarize_metrics(m: dict[str, float | int]) -> dict[str, Any]:
    return {
        "n": m["n"],
        "win_rate_pct": pct(m["win_rate"]),
        "avg_result_pct": pct(m["avg_result"]),
        "sum_result_pct": pct(m["sum_result"]),
        "max_drawdown_pct": pct(m["max_drawdown"]),
        "neg_residual_pct": pct(m["neg_residual"]),
        "bad_tail_rate_pct": pct(m["bad_tail_rate"]),
        "target_rate_pct": pct(m["target_rate"]),
        "buy_count": m["buy_count"],
        "sell_count": m["sell_count"],
    }


def should_adopt(
    baseline: dict[str, Any],
    updated: dict[str, Any],
    old_on_updated: dict[str, float | int],
    new_on_old: dict[str, float | int],
) -> tuple[bool, str]:
    if updated["params"] == baseline["params"]:
        return True, "参数未变化，维持当前稳定模型"
    updated_m = updated["train_metrics"]
    baseline_m = baseline["train_metrics"]
    if int(updated_m["n"]) < 5:
        return False, "新参数交易次数不足"
    if float(updated_m["avg_result"]) < float(old_on_updated["avg_result"]) - 0.0005:
        return False, "新参数在加入今日后的窗口没有优于旧参数"
    if float(new_on_old["avg_result"]) < 0:
        return False, "新参数回放到昨日基准窗口为负"
    if float(updated_m["max_drawdown"]) < float(baseline_m["max_drawdown"]) - 0.008:
        return False, "新参数回撤明显变差"
    if float(updated_m["bad_tail_rate"]) > float(baseline_m["bad_tail_rate"]) + 0.12:
        return False, "新参数坏尾部比例升高过多"
    return True, "新参数通过稳定性门槛"


def model_files(path: Path) -> list[Path]:
    return [path] if path.is_file() else sorted(path.glob("*.json"))


def update_model_file(path: Path, params: dict[str, float]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    current = data.setdefault("params", {})
    for key, value in params.items():
        current[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="models", help="模型 JSON 文件或文件夹")
    parser.add_argument("--since", default="TRADE_DAYS_AGO(31)")
    parser.add_argument("--train-days", type=int, default=30)
    parser.add_argument("--out", default="reports/rolling_optimize_report.json")
    parser.add_argument("--cache-dir", default=".cache/prices")
    parser.add_argument("--write-models", action="store_true", help="稳定性门槛通过后写回模型 JSON")
    parser.add_argument("--only", default="", help="只训练指定股票名或代码")
    args = parser.parse_args()

    models_path = Path(args.models)
    models = load_models(models_path)
    if args.only:
        models = [model for model in models if args.only in (model.name, model.code)]
        if not models:
            raise SystemExit(f"没有匹配模型：{args.only}")
    files_by_code = {}
    for file in model_files(models_path):
        data = json.loads(file.read_text(encoding="utf-8"))
        files_by_code[str(data["code"])] = file
    price_map = collect_data(models, args.since, Path(args.cache_dir))
    common_days = latest_common_days(price_map)
    if len(common_days) < args.train_days + 1:
        raise SystemExit(f"共同交易日不足：{len(common_days)}")

    latest_day = common_days[-1]
    previous_day = common_days[-2]
    baseline_train = common_days[-(args.train_days + 1) : -1]
    updated_train = common_days[-args.train_days :]
    previous_5 = common_days[-6:-1]
    latest_5 = common_days[-5:]

    report = {
        "generated_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_window": "09:30-15:00",
        "latest_day": latest_day,
        "previous_day": previous_day,
        "baseline_train_start": baseline_train[0],
        "baseline_train_end": baseline_train[-1],
        "updated_train_start": updated_train[0],
        "updated_train_end": updated_train[-1],
        "models": {},
    }

    for model in models:
        print(f"optimize {model.name} baseline")
        baseline = optimize_model(model, price_map, baseline_train, [latest_day])
        print(f"optimize {model.name} updated")
        updated = optimize_model(model, price_map, updated_train, [])
        baseline_recent = [trade for day in previous_5 if (trade := simulate_day(model, baseline["params"], price_map, day))]
        updated_recent = [trade for day in latest_5 if (trade := simulate_day(model, updated["params"], price_map, day))]
        cross_old_on_new = [trade for day in updated_train if (trade := simulate_day(model, baseline["params"], price_map, day))]
        cross_new_on_old = [trade for day in baseline_train if (trade := simulate_day(model, updated["params"], price_map, day))]
        old_on_updated_metrics = metrics(cross_old_on_new)
        new_on_old_metrics = metrics(cross_new_on_old)
        adopt, adopt_reason = should_adopt(baseline, updated, old_on_updated_metrics, new_on_old_metrics)
        final_params = updated["params"] if adopt else baseline["params"]
        final_source = "updated" if adopt else "baseline"
        if args.write_models:
            update_model_file(files_by_code[model.code], final_params)

        report["models"][model.name] = {
            "code": model.code,
            "baseline_params": baseline["params"],
            "updated_params": updated["params"],
            "baseline_train": summarize_metrics(baseline["train_metrics"]),
            "baseline_on_latest_day": summarize_metrics(baseline["test_metrics"]),
            "updated_train": summarize_metrics(updated["train_metrics"]),
            "baseline_previous_5d": summarize_metrics(metrics(baseline_recent)),
            "updated_latest_5d": summarize_metrics(metrics(updated_recent)),
            "old_params_on_updated_window": summarize_metrics(old_on_updated_metrics),
            "new_params_on_baseline_window": summarize_metrics(new_on_old_metrics),
            "adopt_updated_params": adopt,
            "adopt_reason": adopt_reason,
            "final_params_source": final_source,
            "final_params": final_params,
            "latest_day_trade_by_baseline": baseline["test_trades"],
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
