from __future__ import annotations

import statistics


def _clamp(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def analyze(bars: list[dict], quote: dict, technical: dict, news: dict | None = None) -> dict:
    valid = [bar for bar in bars if bar.get("close") not in (None, 0)]
    closes = [float(bar["close"]) for bar in valid]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    current_change = float(quote.get("change_pct") or (returns[-1] * 100 if returns else 0.0))
    reference_returns = returns[-21:-1] if len(returns) > 5 else returns[:-1]
    mean_return = statistics.mean(reference_returns) if reference_returns else 0.0
    deviation = statistics.stdev(reference_returns) if len(reference_returns) > 1 else 0.0
    change_z = ((current_change / 100) - mean_return) / deviation if deviation > 1e-8 else current_change / 2.5

    historical_volumes = [float(bar.get("volume") or 0) for bar in valid[-21:-1] if float(bar.get("volume") or 0) > 0]
    current_volume = float((valid[-1].get("volume") if valid else 0) or 0)
    average_volume = statistics.mean(historical_volumes) if historical_volumes else 0.0
    technical_volume = technical.get("relative_volume")
    volume_ratio = float(technical_volume) if technical_volume is not None else current_volume / average_volume if average_volume else None

    historical_ranges = []
    for bar in valid[-21:-1]:
        high, low, close = bar.get("high"), bar.get("low"), bar.get("close")
        if high is not None and low is not None and close:
            historical_ranges.append((float(high) - float(low)) / float(close) * 100)
    latest = valid[-1] if valid else {}
    high, low = latest.get("high"), latest.get("low")
    current_range = (float(high) - float(low)) / float(latest.get("close") or closes[-1]) * 100 if high is not None and low is not None and closes else None
    average_range = statistics.mean(historical_ranges) if historical_ranges else None
    range_ratio = current_range / average_range if current_range is not None and average_range else None

    direction = 1.0 if current_change > 0 else -1.0 if current_change < 0 else 0.0
    components = [("price_anomaly", _clamp(change_z / 3), 0.38)]
    if volume_ratio is not None:
        components.append(("volume_confirmation", direction * _clamp((volume_ratio - 1) / 2, 0, 1), 0.17))
    if range_ratio is not None:
        components.append(("range_expansion", direction * _clamp((range_ratio - 1) / 2, 0, 1), 0.10))
    if technical.get("score") is not None:
        components.append(("technical_structure", _clamp(float(technical["score"]) / 100), 0.15))
    if news and news.get("evidence_count"):
        components.append(("related_news", _clamp(float(news.get("score") or 0)), 0.20))
    weight_sum = sum(weight for _, _, weight in components) or 1.0
    score = sum(value * weight for _, value, weight in components) / weight_sum * 100

    price_intensity = min(1.0, abs(change_z) / 3)
    volume_intensity = min(1.0, max(0.0, (volume_ratio or 1) - 1) / 2)
    range_intensity = min(1.0, max(0.0, (range_ratio or 1) - 1) / 2)
    abnormal_score = 100 * (0.58 * price_intensity + 0.25 * volume_intensity + 0.17 * range_intensity)
    abnormal = abnormal_score >= 42
    if score >= 25:
        label = "bullish"
    elif score <= -25:
        label = "bearish"
    else:
        label = "neutral"
    if abnormal and direction > 0:
        regime = "异常上行"
    elif abnormal and direction < 0:
        regime = "异常下行"
    elif (volume_ratio or 0) >= 1.5:
        regime = "量能活跃"
    else:
        regime = "常态波动"

    factors = [
        {"key": "price_anomaly", "label": "涨跌异动", "value": current_change, "display": f"{current_change:+.2f}% · Z {change_z:+.2f}", "direction": "positive" if change_z > 0 else "negative" if change_z < 0 else "neutral", "source": quote.get("source")},
        {"key": "volume_confirmation", "label": "量能确认", "value": volume_ratio, "display": f"近 20 日均量的 {volume_ratio:.2f}×" if volume_ratio is not None else "样本不足", "direction": "positive" if volume_ratio is not None and volume_ratio >= 1.2 else "neutral", "source": quote.get("source")},
        {"key": "range_expansion", "label": "振幅扩张", "value": range_ratio, "display": f"常态振幅的 {range_ratio:.2f}×" if range_ratio is not None else "样本不足", "direction": "negative" if range_ratio is not None and range_ratio >= 1.8 else "neutral", "source": quote.get("source")},
        {"key": "related_news", "label": "关联内容", "value": (news or {}).get("score"), "display": f"{(news or {}).get('article_count', 0)} 条内容 · {(news or {}).get('evidence_count', 0)} 条有效证据", "direction": (news or {}).get("label", "neutral"), "source": "GDELT / Yahoo Finance"},
    ]
    data_coverage = sum(value is not None for value in (change_z, volume_ratio, range_ratio, technical.get("score"))) / 4
    news_coverage = min(1.0, float((news or {}).get("source_count") or 0) / 2)
    confidence = 100 * (0.72 * data_coverage + 0.28 * news_coverage)
    return {
        "score": round(score, 2),
        "label": label,
        "regime": regime,
        "confidence": round(confidence, 2),
        "confidence_kind": "market_evidence_coverage_not_return_probability",
        "abnormal": {"detected": abnormal, "score": round(abnormal_score, 2), "change_zscore": round(change_z, 3)},
        "metrics": {"change_pct": current_change, "volume_ratio_20d": volume_ratio, "range_ratio_20d": range_ratio},
        "factors": factors,
        "related_evidence": (news or {}).get("evidence", [])[:5],
        "method": "deterministic_price_volume_range_technical_news_v2",
    }
