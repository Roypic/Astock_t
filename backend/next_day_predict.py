from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from model_engine import BEIJING_TZ, INDEX_CODES, TModel, load_models
from rolling_optimize import collect_data, nearest_at


FEATURE_NAMES = (
    ("ret_1d", "当日涨跌"),
    ("ret_3d", "3日动量"),
    ("ret_5d", "5日动量"),
    ("range_1d", "当日振幅"),
    ("close_pos", "收盘区间位置"),
    ("vol_ratio_5d", "5日量能比"),
    ("amount_ratio_5d", "5日成交额比"),
    ("morning_ret", "上午强弱"),
    ("late_ret", "尾盘强弱"),
    ("market_ret", "大盘强弱"),
    ("basket_ret", "相似股篮子"),
    ("relative_ret", "相对篮子"),
    ("ma5_gap", "偏离MA5"),
    ("ma10_gap", "偏离MA10"),
    ("ma_slope", "MA5/MA10斜率"),
)


def build_prediction_report(
    models: list[TModel],
    since: str = "TRADE_DAYS_AGO(121)",
    cache_dir: Path | None = None,
) -> str:
    cache = cache_dir or Path(".cache/next_day")
    price_map = collect_data(models, since, cache)
    now = datetime.now(BEIJING_TZ)
    lines = [
        f"明日走势预测（北京时间 {now.strftime('%Y-%m-%d %H:%M')}）",
        f"训练窗口：{since}；模型：轻量逻辑回归 + 近邻情景分布；标签：下一交易日相对当日收盘的涨跌/冲高/下探。",
        "说明：只用历史量价、大盘和相似股，不含明日消息面；这是概率辅助，不是投资建议。",
        "",
    ]
    for model in models:
        try:
            result = predict_one(model, price_map)
            lines.extend(format_prediction(result))
        except Exception as exc:
            lines.extend([f"【{model.name}】预测失败：{exc}", ""])
    return "\n".join(lines).rstrip() + "\n"


def predict_one(model: TModel, price_map: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    target_days = sorted(price_map.get(model.code, {}))
    if len(target_days) < 35:
        raise RuntimeError(f"有效交易日不足：{len(target_days)}")
    samples = []
    for index in range(20, len(target_days) - 1):
        day = target_days[index]
        next_day = target_days[index + 1]
        feature = feature_for_day(model, price_map, target_days, index)
        if feature is None:
            continue
        today_bar = day_bar(price_map[model.code][day])
        next_bar = day_bar(price_map[model.code][next_day])
        if not today_bar or not next_bar or today_bar["close"] <= 0:
            continue
        next_return = next_bar["close"] / today_bar["close"] - 1
        next_high = next_bar["high"] / today_bar["close"] - 1
        next_low = next_bar["low"] / today_bar["close"] - 1
        samples.append(
            {
                "day": day,
                "features": feature,
                "next_return": next_return,
                "next_high": next_high,
                "next_low": next_low,
                "up": 1 if next_return > 0 else 0,
                "strong": 1 if next_high >= 0.015 else 0,
                "drawdown": 1 if next_low <= -0.015 else 0,
            }
        )
    if len(samples) < 25:
        raise RuntimeError(f"训练样本不足：{len(samples)}")

    latest_feature = feature_for_day(model, price_map, target_days, len(target_days) - 1)
    if latest_feature is None:
        raise RuntimeError("最新交易日特征不足")
    latest_day = target_days[-1]
    up_model = train_classifier([s["features"] for s in samples], [s["up"] for s in samples])
    strong_model = train_classifier([s["features"] for s in samples], [s["strong"] for s in samples])
    drawdown_model = train_classifier([s["features"] for s in samples], [s["drawdown"] for s in samples])
    up_prob = predict_probability(up_model, latest_feature)
    strong_prob = predict_probability(strong_model, latest_feature)
    drawdown_prob = predict_probability(drawdown_model, latest_feature)
    analogs = nearest_analogs(samples, latest_feature, up_model["means"], up_model["stds"], k=10)
    analog_returns = [float(item["next_return"]) for item in analogs]
    expected_return = statistics.mean(analog_returns) if analog_returns else 0.0
    low_q, mid_q, high_q = quantiles(analog_returns, (0.2, 0.5, 0.8))
    verdict, risk = classify_view(up_prob, strong_prob, drawdown_prob, expected_return)
    drivers = top_drivers(up_model, latest_feature)
    latest_bar = day_bar(price_map[model.code][latest_day]) or {}
    return {
        "name": model.name,
        "code": model.code,
        "latest_day": latest_day,
        "latest_close": latest_bar.get("close", "-"),
        "sample_count": len(samples),
        "up_prob": round(up_prob * 100, 1),
        "strong_prob": round(strong_prob * 100, 1),
        "drawdown_prob": round(drawdown_prob * 100, 1),
        "expected_return_pct": round(expected_return * 100, 2),
        "q20_pct": round(low_q * 100, 2),
        "q50_pct": round(mid_q * 100, 2),
        "q80_pct": round(high_q * 100, 2),
        "verdict": verdict,
        "risk": risk,
        "up_validation": up_model["validation"],
        "drivers": drivers,
        "analogs": [{"day": item["day"], "next_return_pct": round(float(item["next_return"]) * 100, 2)} for item in analogs[:5]],
    }


def day_bar(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    if not rows:
        return None
    prices = [float(row["price"]) for row in rows if row.get("price")]
    if not prices:
        return None
    return {
        "open": prices[0],
        "close": prices[-1],
        "high": max(prices),
        "low": min(prices),
        "volume": sum(float(row.get("volume") or 0) for row in rows),
        "amount": sum(float(row.get("amount") or 0) for row in rows),
        "morning": float((nearest_at(rows, "11:30") or rows[0])["price"]),
        "late_base": float((nearest_at(rows, "14:30") or rows[0])["price"]),
    }


def feature_for_day(
    model: TModel,
    price_map: dict[str, dict[str, list[dict[str, Any]]]],
    target_days: list[str],
    index: int,
) -> list[float] | None:
    day = target_days[index]
    bars = [day_bar(price_map[model.code].get(target_days[i], [])) for i in range(max(0, index - 20), index + 1)]
    if any(bar is None for bar in bars[-6:]):
        return None
    current = bars[-1] or {}
    prev = bars[-2] or {}
    close = float(current["close"])
    if close <= 0 or float(prev["close"]) <= 0:
        return None
    closes = [float(bar["close"]) for bar in bars if bar]
    volumes = [float(bar["volume"]) for bar in bars if bar]
    amounts = [float(bar["amount"]) for bar in bars if bar]
    high = float(current["high"])
    low = float(current["low"])
    open_price = float(current["open"])
    day_range = max(high - low, close * 0.001)
    ret_1d = close / float(prev["close"]) - 1
    ret_3d = close / closes[-4] - 1 if len(closes) >= 4 and closes[-4] > 0 else 0.0
    ret_5d = close / closes[-6] - 1 if len(closes) >= 6 and closes[-6] > 0 else 0.0
    range_1d = high / low - 1 if low > 0 else 0.0
    close_pos = (close - low) / day_range
    vol_avg = statistics.mean(volumes[-6:-1]) if len(volumes) >= 6 else statistics.mean(volumes)
    amount_avg = statistics.mean(amounts[-6:-1]) if len(amounts) >= 6 else statistics.mean(amounts)
    vol_ratio = float(current["volume"]) / vol_avg if vol_avg > 0 else 1.0
    amount_ratio = float(current["amount"]) / amount_avg if amount_avg > 0 else 1.0
    morning_ret = float(current["morning"]) / open_price - 1 if open_price > 0 else 0.0
    late_ret = close / float(current["late_base"]) - 1 if float(current["late_base"]) > 0 else 0.0
    market_ret = weighted_market_return(price_map, day)
    basket_ret = basket_day_return(model, price_map, day)
    relative_ret = ret_1d - basket_ret
    ma5 = statistics.mean(closes[-5:])
    ma10 = statistics.mean(closes[-10:]) if len(closes) >= 10 else ma5
    ma5_gap = close / ma5 - 1 if ma5 > 0 else 0.0
    ma10_gap = close / ma10 - 1 if ma10 > 0 else 0.0
    ma_slope = ma5 / ma10 - 1 if ma10 > 0 else 0.0
    return [
        ret_1d,
        ret_3d,
        ret_5d,
        range_1d,
        close_pos,
        vol_ratio,
        amount_ratio,
        morning_ret,
        late_ret,
        market_ret,
        basket_ret,
        relative_ret,
        ma5_gap,
        ma10_gap,
        ma_slope,
    ]


def weighted_market_return(price_map: dict[str, dict[str, list[dict[str, Any]]]], day: str) -> float:
    weights = {"000001.XSHG": 0.25, "399001.XSHE": 0.30, "399006.XSHE": 0.45}
    values = []
    for code, weight in weights.items():
        bar = day_bar(price_map.get(code, {}).get(day, []))
        if bar and bar["open"] > 0:
            values.append(weight * (bar["close"] / bar["open"] - 1))
    return sum(values) if values else 0.0


def basket_day_return(model: TModel, price_map: dict[str, dict[str, list[dict[str, Any]]]], day: str) -> float:
    values = []
    for peer in model.basket:
        bar = day_bar(price_map.get(peer.code, {}).get(day, []))
        if bar and bar["open"] > 0:
            values.append(bar["close"] / bar["open"] - 1)
    return statistics.mean(values) if values else 0.0


def train_classifier(features: list[list[float]], labels: list[int]) -> dict[str, Any]:
    n = len(features)
    split = max(10, int(n * 0.75))
    train_x, train_y = features[:split], labels[:split]
    valid_x, valid_y = features[split:], labels[split:]
    means = [statistics.mean(col) for col in zip(*train_x)]
    stds = [statistics.pstdev(col) or 1.0 for col in zip(*train_x)]
    xz = [standardize(row, means, stds) for row in train_x]
    pos_rate = sum(train_y) / max(1, len(train_y))
    if pos_rate in (0.0, 1.0):
        weights = [0.0] * len(means)
        bias = logit(min(0.98, max(0.02, pos_rate)))
    else:
        weights = [0.0] * len(means)
        bias = logit(pos_rate)
        lr = 0.08
        l2 = 0.015
        for _ in range(700):
            grad_w = [0.0] * len(weights)
            grad_b = 0.0
            for row, label in zip(xz, train_y):
                pred = sigmoid(dot(weights, row) + bias)
                err = pred - label
                grad_b += err
                for idx, value in enumerate(row):
                    grad_w[idx] += err * value
            m = max(1, len(xz))
            bias -= lr * grad_b / m
            for idx in range(len(weights)):
                weights[idx] -= lr * (grad_w[idx] / m + l2 * weights[idx])
    validation = validate(weights, bias, means, stds, valid_x, valid_y)
    return {"weights": weights, "bias": bias, "means": means, "stds": stds, "validation": validation}


def validate(
    weights: list[float],
    bias: float,
    means: list[float],
    stds: list[float],
    valid_x: list[list[float]],
    valid_y: list[int],
) -> dict[str, Any]:
    if not valid_x:
        return {"n": 0, "accuracy_pct": 0.0, "base_rate_pct": 0.0}
    correct = 0
    for row, label in zip(valid_x, valid_y):
        prob = sigmoid(dot(weights, standardize(row, means, stds)) + bias)
        pred = 1 if prob >= 0.5 else 0
        correct += 1 if pred == label else 0
    return {
        "n": len(valid_x),
        "accuracy_pct": round(correct / len(valid_x) * 100, 1),
        "base_rate_pct": round(sum(valid_y) / len(valid_y) * 100, 1),
    }


def predict_probability(model: dict[str, Any], feature: list[float]) -> float:
    row = standardize(feature, model["means"], model["stds"])
    return sigmoid(dot(model["weights"], row) + float(model["bias"]))


def nearest_analogs(
    samples: list[dict[str, Any]],
    feature: list[float],
    means: list[float],
    stds: list[float],
    k: int = 10,
) -> list[dict[str, Any]]:
    target = standardize(feature, means, stds)
    scored = []
    for sample in samples:
        row = standardize(sample["features"], means, stds)
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(row, target)))
        scored.append((dist, sample))
    return [sample for _dist, sample in sorted(scored, key=lambda item: item[0])[:k]]


def top_drivers(model: dict[str, Any], feature: list[float]) -> list[str]:
    row = standardize(feature, model["means"], model["stds"])
    impacts = []
    for idx, (value, weight) in enumerate(zip(row, model["weights"])):
        impacts.append((abs(value * weight), value * weight, FEATURE_NAMES[idx][1]))
    lines = []
    for _abs_impact, impact, name in sorted(impacts, reverse=True)[:4]:
        direction = "利多" if impact >= 0 else "利空"
        lines.append(f"{name}{direction}")
    return lines


def classify_view(up_prob: float, strong_prob: float, drawdown_prob: float, expected_return: float) -> tuple[str, str]:
    if up_prob >= 0.58 and expected_return > 0 and drawdown_prob <= 0.48:
        verdict = "偏强"
    elif drawdown_prob >= 0.56 and up_prob <= 0.52:
        verdict = "偏弱"
    elif strong_prob >= 0.48 and drawdown_prob >= 0.48:
        verdict = "高波动"
    else:
        verdict = "震荡"
    risk = "高" if drawdown_prob >= 0.58 else "中" if drawdown_prob >= 0.42 else "低"
    return verdict, risk


def format_prediction(result: dict[str, Any]) -> list[str]:
    validation = result.get("up_validation", {})
    analogs = result.get("analogs", [])
    analog_text = "、".join(f"{item['day']}({item['next_return_pct']:+.2f}%)" for item in analogs) or "-"
    drivers = "、".join(result.get("drivers", [])) or "-"
    return [
        f"【{result['name']} {result['code']}】最新交易日 {result['latest_day']}，收盘 {result['latest_close']}",
        f"- 明日倾向：{result['verdict']}；回撤风险：{result['risk']}；上涨概率 {result['up_prob']}%，冲高1.5%概率 {result['strong_prob']}%，下探1.5%概率 {result['drawdown_prob']}%。",
        f"- 情景收益：近邻均值 {result['expected_return_pct']:+.2f}%；20/50/80分位 {result['q20_pct']:+.2f}% / {result['q50_pct']:+.2f}% / {result['q80_pct']:+.2f}%。",
        f"- 训练样本：{result['sample_count']}；最近验证上涨方向准确率 {validation.get('accuracy_pct', '-')}%（样本 {validation.get('n', 0)}，基准上涨率 {validation.get('base_rate_pct', '-')}%）。",
        f"- 主要驱动：{drivers}。",
        f"- 相似历史日：{analog_text}。",
        "",
    ]


def standardize(row: list[float], means: list[float], stds: list[float]) -> list[float]:
    return [(value - mean) / std for value, mean, std in zip(row, means, stds)]


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(60.0, value))
        return 1 / (1 + z)
    z = math.exp(max(-60.0, value))
    return z / (1 + z)


def logit(prob: float) -> float:
    prob = min(0.999, max(0.001, prob))
    return math.log(prob / (1 - prob))


def quantiles(values: list[float], points: tuple[float, ...]) -> tuple[float, ...]:
    if not values:
        return tuple(0.0 for _ in points)
    sorted_values = sorted(values)
    result = []
    for point in points:
        pos = point * (len(sorted_values) - 1)
        low = int(math.floor(pos))
        high = int(math.ceil(pos))
        if low == high:
            result.append(sorted_values[low])
        else:
            weight = pos - low
            result.append(sorted_values[low] * (1 - weight) + sorted_values[high] * weight)
    return tuple(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="models")
    parser.add_argument("--only", default="")
    parser.add_argument("--since", default="TRADE_DAYS_AGO(121)")
    parser.add_argument("--cache-dir", default=".cache/next_day")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    models = load_models(Path(args.models))
    if args.only:
        models = [model for model in models if args.only in (model.name, model.code)]
    report = build_prediction_report(models, since=args.since, cache_dir=Path(args.cache_dir))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
