from __future__ import annotations

import math
import statistics


def sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    window = changes[-period:]
    gains = sum(max(change, 0) for change in window) / period
    losses = sum(max(-change, 0) for change in window) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def atr(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) <= period:
        return None
    ranges = []
    for index in range(1, len(bars)):
        current = bars[index]
        previous_close = bars[index - 1]["close"]
        if current.get("high") is None or current.get("low") is None:
            continue
        ranges.append(max(current["high"] - current["low"], abs(current["high"] - previous_close), abs(current["low"] - previous_close)))
    return sum(ranges[-period:]) / period if len(ranges) >= period else None


def _level_windows(interval: str | None) -> tuple[int, int, str, str]:
    value = str(interval or "1d").lower()
    if value.endswith("m") and value[:-1].isdigit():
        minutes = max(1, int(value[:-1]))
        ultra = max(6, min(18, round(60 / minutes)))
        short = max(20, min(54, round(240 / minutes)))
        return ultra, short, f"约 {ultra * minutes} 分钟", f"约 {short * minutes} 分钟"
    if value in {"60m", "90m", "1h"}:
        return 6, 20, "约 6 小时", "约 20 小时"
    return 5, 20, "最近 5 个交易日", "最近 20 个交易日"


def _pick_level(
    bars: list[dict],
    price: float,
    atr_value: float | None,
    *,
    kind: str,
    window: int,
    horizon: str,
    window_label: str,
    interval: str | None,
    exclude: float | None = None,
) -> dict:
    recent = bars[-max(3, min(window, len(bars))):]
    lows = [float(bar.get("low") if bar.get("low") is not None else bar["close"]) for bar in recent]
    highs = [float(bar.get("high") if bar.get("high") is not None else bar["close"]) for bar in recent]
    closes = [float(bar["close"]) for bar in recent]
    scale = max(float(atr_value or 0), abs(price) * .0025, 1e-9)
    tolerance = max(scale * .18, abs(price) * .0007, 1e-9)
    points = lows if kind == "support" else highs
    candidates = []

    for index in range(1, len(points) - 1):
        left = points[max(0, index - 2):index]
        right = points[index + 1:min(len(points), index + 3)]
        is_turn = points[index] <= min(left + right) if kind == "support" else points[index] >= max(left + right)
        if is_turn:
            candidates.append({"value": points[index], "method": "局部转折", "recency": (index + 1) / len(points), "method_weight": 2.2})

    candidates.append({
        "value": min(lows) if kind == "support" else max(highs),
        "method": "窗口极值",
        "recency": 1.0,
        "method_weight": .8,
    })
    average_period = max(3, min(20, len(closes) // 2))
    average = ema_series(closes, average_period)[-1]
    candidates.append({"value": average, "method": f"EMA{average_period}", "recency": 1.0, "method_weight": 1.3})

    eligible = []
    for candidate in candidates:
        value = candidate["value"]
        correct_side = value < price - tolerance * .1 if kind == "support" else value > price + tolerance * .1
        if not correct_side or (exclude is not None and abs(value - exclude) <= tolerance):
            continue
        touches = sum(abs(point - value) <= tolerance for point in points)
        proximity = max(0.0, 3.5 - abs(price - value) / scale)
        score = touches * 1.65 + proximity + candidate["recency"] * 1.8 + candidate["method_weight"]
        eligible.append({**candidate, "touches": touches, "score": score})

    estimated = not eligible
    if eligible:
        selected = max(eligible, key=lambda item: (item["score"], -abs(price - item["value"])))
        value = selected["value"]
        method = selected["method"]
        touches = selected["touches"]
    else:
        multiplier = .65 if horizon == "ultra_short" else 1.35
        value = price + (scale * multiplier if kind == "resistance" else -scale * multiplier)
        method = "ATR14 动态边界"
        touches = 0

    horizon_label = "超短线" if horizon == "ultra_short" else "短线"
    kind_label = "支撑" if kind == "support" else "压力"
    return {
        "label": f"{horizon_label}{kind_label}",
        "short_label": f"{'超短' if horizon == 'ultra_short' else '短线'}{'支' if kind == 'support' else '压'}",
        "value": value,
        "kind": kind,
        "horizon": horizon,
        "interval": interval or "1d",
        "window_bars": len(recent),
        "window_label": window_label,
        "distance_pct": (value / price - 1) * 100 if price else None,
        "touches": touches,
        "method": "causal_swing_atr_v1",
        "method_label": method,
        "estimated_boundary": estimated,
        "source": "行情 K 线 · ATR14 · 已确认局部转折",
        "as_of": recent[-1].get("time") or recent[-1].get("timestamp"),
    }


def dynamic_levels(bars: list[dict], price: float, atr_value: float | None, interval: str | None = None) -> list[dict]:
    ultra_window, short_window, ultra_label, short_label = _level_windows(interval)
    ultra_support = _pick_level(bars, price, atr_value, kind="support", window=ultra_window, horizon="ultra_short", window_label=ultra_label, interval=interval)
    ultra_resistance = _pick_level(bars, price, atr_value, kind="resistance", window=ultra_window, horizon="ultra_short", window_label=ultra_label, interval=interval)
    short_support = _pick_level(bars, price, atr_value, kind="support", window=short_window, horizon="short", window_label=short_label, interval=interval, exclude=ultra_support["value"])
    short_resistance = _pick_level(bars, price, atr_value, kind="resistance", window=short_window, horizon="short", window_label=short_label, interval=interval, exclude=ultra_resistance["value"])
    return [ultra_support, ultra_resistance, short_support, short_resistance]


def analyze(bars: list[dict], current_price: float | None = None, interval: str | None = None) -> dict:
    valid = [bar for bar in bars if bar.get("close") is not None]
    closes = [float(bar["close"]) for bar in valid]
    if len(closes) < 20:
        return {"status": "insufficient_data", "required_bars": 20, "available_bars": len(closes), "signals": [], "facts": []}
    price = float(current_price if current_price is not None else closes[-1])
    averages = {f"ma{period}": sma(closes, period) for period in (5, 10, 20, 60, 120)}
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_line_series = [a - b for a, b in zip(ema12, ema26)]
    signal_series = ema_series(macd_line_series, 9)
    macd_line = macd_line_series[-1]
    macd_signal = signal_series[-1]
    macd_histogram = macd_line - macd_signal
    rsi_value = rsi(closes)
    atr_value = atr(valid)
    returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
    volatility = statistics.stdev(returns[-20:]) * math.sqrt(252) * 100 if len(returns) >= 20 else None
    volumes = [float(bar.get("volume") or 0) for bar in valid]
    base_volume = sma(volumes[:-1], 20) if len(volumes) > 20 else None
    volume_ratio = volumes[-1] / base_volume if base_volume else None

    bullish, bearish, signals, facts = [], [], [], []
    if averages["ma20"]:
        gap = (price / averages["ma20"] - 1) * 100
        direction = "above" if gap >= 0 else "below"
        (bullish if gap >= 0 else bearish).append("price_vs_ma20")
        signals.append({"key": "price_vs_ma20", "direction": "bullish" if gap >= 0 else "bearish", "label": f"Price {abs(gap):.2f}% {direction} MA20", "value": gap})
        facts.append(f"MA20 {averages['ma20']:.3f}; price is {abs(gap):.2f}% {direction} it")
    if rsi_value is not None:
        direction = "bearish" if rsi_value >= 70 else "bullish" if rsi_value <= 30 else "neutral"
        if direction == "bullish": bullish.append("rsi_oversold")
        if direction == "bearish": bearish.append("rsi_overbought")
        signals.append({"key": "rsi14", "direction": direction, "label": f"RSI 14 at {rsi_value:.1f}", "value": rsi_value})
        facts.append(f"RSI14 {rsi_value:.2f}")
    macd_direction = "bullish" if macd_histogram >= 0 else "bearish"
    (bullish if macd_histogram >= 0 else bearish).append("macd")
    signals.append({"key": "macd", "direction": macd_direction, "label": f"MACD histogram {macd_histogram:.4f}", "value": macd_histogram})
    facts.append(f"MACD {macd_line:.4f}, signal {macd_signal:.4f}, histogram {macd_histogram:.4f}")
    if volume_ratio is not None:
        # Low or zero volume on the still-forming intraday bar is not bearish;
        # volume only confirms direction when it expands.
        direction = "bullish" if volume_ratio >= 1.2 else "neutral"
        if direction == "bullish": bullish.append("volume_expansion")
        signals.append({"key": "relative_volume", "direction": direction, "label": f"Relative volume {volume_ratio:.2f}×", "value": volume_ratio})
        facts.append(f"Relative volume {volume_ratio:.3f}x of the prior 20-bar average")

    # Continuous strength scoring: each signal contributes its measured
    # magnitude instead of a flat +/-25, so the composite score can take any
    # value in [-100, 100].
    contributions = []
    if averages["ma20"]:
        contributions.append(max(-25.0, min(25.0, gap / 3.0 * 25.0)))
    if rsi_value is not None:
        rsi_pull = max(-1.0, min(1.0, (50.0 - rsi_value) / 20.0))
        # Overbought extremes flip to bearish; oversold to bullish.
        if rsi_value >= 70:
            rsi_pull = -min(1.0, (rsi_value - 70) / 15.0 + 0.5)
        elif rsi_value <= 30:
            rsi_pull = min(1.0, (30 - rsi_value) / 15.0 + 0.5)
        contributions.append(rsi_pull * 25.0)
    atr_scale = atr_value if atr_value else max(abs(price) * 0.002, 1e-9)
    contributions.append(max(-25.0, min(25.0, macd_histogram / (2.0 * atr_scale) * 25.0)))
    if volume_ratio is not None and volume_ratio >= 1.2:
        contributions.append(max(0.0, min(25.0, (volume_ratio - 1.0) / 1.0 * 25.0)) * (1 if macd_histogram >= 0 else -1))
    score = round(max(-100.0, min(100.0, sum(contributions))), 2)
    if score >= 40: stance = "bullish"
    elif score <= -40: stance = "bearish"
    else: stance = "mixed"
    recent = valid[-20:]
    recent_low = min(float(bar.get("low") or bar["close"]) for bar in recent)
    recent_high = max(float(bar.get("high") or bar["close"]) for bar in recent)
    levels = []
    for label, value in [("20-bar low", recent_low), ("20-bar high", recent_high)] + [(key.upper(), value) for key, value in averages.items() if value]:
        levels.append({"label": label, "value": value, "kind": "support" if value <= price else "resistance", "distance_pct": (value / price - 1) * 100})
    live_levels = dynamic_levels(valid, price, atr_value, interval)
    material_conflict = len(bullish) >= 2 and len(bearish) >= 2
    return {
        "status": "ready",
        "stance": stance,
        "score": score,
        "moving_averages": averages,
        "rsi14": rsi_value,
        "macd": {"line": macd_line, "signal": macd_signal, "histogram": macd_histogram, "direction": macd_direction},
        "atr14": atr_value,
        "atr_pct": atr_value / price * 100 if atr_value and price else None,
        "annualized_volatility_pct": volatility,
        "relative_volume": volume_ratio,
        "signals": signals,
        "facts": facts,
        "levels": sorted(levels, key=lambda item: abs(item["distance_pct"])),
        "dynamic_levels": live_levels,
        "levels_method": {
            "name": "causal_swing_atr_v1",
            "description": "仅使用当前时点及之前的 K 线，以已确认局部转折、EMA 与 ATR14 动态边界计算。",
            "source": "当前行情 K 线",
            "as_of": valid[-1].get("time") or valid[-1].get("timestamp"),
        },
        "signal_conflict": {"material": material_conflict, "bullish": bullish, "bearish": bearish},
    }
