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


def analyze(bars: list[dict], current_price: float | None = None) -> dict:
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

    score = max(-100, min(100, (len(bullish) - len(bearish)) * 25))
    if score >= 40: stance = "bullish"
    elif score <= -40: stance = "bearish"
    else: stance = "mixed"
    recent = valid[-20:]
    recent_low = min(float(bar.get("low") or bar["close"]) for bar in recent)
    recent_high = max(float(bar.get("high") or bar["close"]) for bar in recent)
    levels = []
    for label, value in [("20-bar low", recent_low), ("20-bar high", recent_high)] + [(key.upper(), value) for key, value in averages.items() if value]:
        levels.append({"label": label, "value": value, "kind": "support" if value <= price else "resistance", "distance_pct": (value / price - 1) * 100})
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
        "signal_conflict": {"material": material_conflict, "bullish": bullish, "bearish": bearish},
    }
