from __future__ import annotations

import hashlib


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tier(value: float, threshold: float) -> int:
    return max(1, min(3, int(abs(value) / max(threshold, 1e-8))))


def _volume_text(relative_volume: float) -> str:
    # A zero-volume forming bar carries no volume evidence yet; presenting it
    # as "0.00x" reads like a measured collapse in activity.
    if relative_volume > 0:
        return f"相对量能 {relative_volume:.2f}×"
    return "量能仍在形成"


def scan(data: dict, thresholds: dict) -> dict:
    asset = data.get("asset") or {}
    quote = data.get("quote") or {}
    technical = data.get("technical") or {}
    forecast = data.get("ml_forecast") or {}
    evaluation = forecast.get("evaluation") or {}
    next_forecast = forecast.get("next_forecast") or {}
    sentiment = data.get("market_sentiment") or {}
    confidence = _number((forecast.get("confidence") or {}).get("score"))
    change_pct = _number(quote.get("change_pct"))
    technical_score = _number(technical.get("score"))
    relative_volume = _number(technical.get("relative_volume"))
    predicted_return = _number(next_forecast.get("predicted_return_pct"))
    sentiment_score = _number(sentiment.get("score"))
    forecast_threshold = _number(thresholds.get("forecast_pct"), 0.7)
    price_threshold = _number(thresholds.get("price_change_pct"), 2.0)
    volume_threshold = _number(thresholds.get("volume_ratio"), 1.8)
    matches = []

    anomaly_triggered = abs(change_pct) >= price_threshold or relative_volume >= volume_threshold
    if anomaly_triggered:
        direction = "up" if change_pct > 0 else "down" if change_pct < 0 else "active"
        intensity = max(abs(change_pct) / max(price_threshold, 0.01), relative_volume / max(volume_threshold, 0.01))
        matches.append({
            "category": "anomaly", "direction": direction, "score": round(min(100.0, intensity * 55), 2),
            "trigger": "price_change" if abs(change_pct) >= price_threshold else "relative_volume",
            "title": f"{asset.get('symbol')} 出现{'上行' if direction == 'up' else '下行' if direction == 'down' else '量能'}异动",
            "summary": f"日内变动 {change_pct:+.2f}% · {_volume_text(relative_volume)}",
            "evidence": [f"价格变动阈值 {price_threshold:.2f}%", f"量能阈值 {volume_threshold:.2f}×"],
            "tier": _tier(max(abs(change_pct), relative_volume), max(price_threshold, volume_threshold)),
        })

    phase_lag = evaluation.get("phase_lag_bars")
    forecast_ready = (
        forecast.get("status") == "ready"
        and confidence >= 35
        and evaluation.get("validation_passed") is True
        and (phase_lag is None or _number(phase_lag) >= 0)
        and next_forecast.get("publishable", True) is True
    )
    forecast_up_consensus = forecast_ready and predicted_return >= forecast_threshold and technical_score >= 0 and sentiment_score >= 0
    potential_triggered = forecast_up_consensus or (technical_score >= 65 and change_pct > 0 and sentiment_score >= 0)
    if potential_triggered:
        forecast_triggered = forecast_up_consensus
        forecast_strength = min(2.0, predicted_return / max(forecast_threshold, 0.01)) if forecast_triggered else 0.0
        consensus = .45 * forecast_strength + .35 * max(0.0, technical_score) / 100 + .20 * max(0.0, sentiment_score) / 100
        strength = max(consensus, max(0.0, technical_score) / 100)
        matches.append({
            "category": "potential", "direction": "up", "score": round(min(100.0, strength * 70), 2),
            "trigger": "forward_forecast" if forecast_triggered else "technical_structure",
            "title": f"{asset.get('symbol')} 上行条件形成",
            "summary": f"{forecast.get('horizon_label') or '前瞻窗口'} {predicted_return:+.2f}% · 技术 {technical_score:+.0f} · 情绪 {sentiment_score:+.0f}" if forecast_triggered else f"技术结构 {technical_score:+.0f} · 情绪 {sentiment_score:+.0f} · 日内 {change_pct:+.2f}%",
            "evidence": [f"模型校准质量 {confidence:.0f}/100", f"技术与情绪方向一致", f"模型族 {(forecast.get('ensemble') or {}).get('profile') or 'market'}"] if forecast_triggered else ["本次由价格与技术结构触发", "实时情绪未与方向冲突"],
            "tier": _tier(max(predicted_return, technical_score / 100), max(forecast_threshold, 0.65)),
        })

    forecast_down_consensus = forecast_ready and predicted_return <= -forecast_threshold and technical_score <= 0 and sentiment_score <= 0
    risk_triggered = forecast_down_consensus or (technical_score <= -65 and change_pct < 0 and sentiment_score <= 0)
    if risk_triggered:
        forecast_triggered = forecast_down_consensus
        forecast_strength = min(2.0, abs(predicted_return) / max(forecast_threshold, 0.01)) if forecast_triggered else 0.0
        consensus = .45 * forecast_strength + .35 * max(0.0, -technical_score) / 100 + .20 * max(0.0, -sentiment_score) / 100
        strength = max(consensus, max(0.0, -technical_score) / 100)
        matches.append({
            "category": "risk", "direction": "down", "score": round(min(100.0, strength * 58), 2),
            "trigger": "forward_forecast" if forecast_triggered else "technical_structure",
            "title": f"{asset.get('symbol')} 风险信号增强",
            "summary": f"{forecast.get('horizon_label') or '前瞻窗口'} {predicted_return:+.2f}% · 技术 {technical_score:+.0f} · 情绪 {sentiment_score:+.0f}" if forecast_triggered else f"技术结构 {technical_score:+.0f} · 情绪 {sentiment_score:+.0f} · 日内 {change_pct:+.2f}%",
            "evidence": [f"模型校准质量 {confidence:.0f}/100", f"技术与情绪方向一致", f"模型族 {(forecast.get('ensemble') or {}).get('profile') or 'market'}"] if forecast_triggered else ["本次由价格与技术结构触发", "实时情绪未与方向冲突"],
            "tier": _tier(max(abs(predicted_return), abs(technical_score) / 100), max(forecast_threshold, 0.65)),
        })

    priority = {"risk": 3, "anomaly": 2, "potential": 1}
    primary = max(matches, key=lambda item: (priority[item["category"]], item["score"])) if matches else None
    status = {
        "category": primary["category"] if primary else "normal",
        "label": {"risk": "风险预警", "anomaly": "异动", "potential": "潜力观察"}.get((primary or {}).get("category"), "监测中"),
        "tone": {"risk": "negative", "anomaly": "warning", "potential": "positive"}.get((primary or {}).get("category"), "neutral"),
        "score": primary["score"] if primary else 0,
    }
    session_key = str(quote.get("as_of") or next_forecast.get("origin_time") or "current")[:10]
    source = quote.get("source") or "market provider"
    alerts = []
    for match in matches:
        raw_id = f"{asset.get('market')}:{asset.get('symbol')}:{match['category']}:{match['direction']}:{session_key}:{match['tier']}"
        alerts.append({
            "id": "alert_" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:18],
            "asset": asset, "category": match["category"], "direction": match["direction"],
            "severity": "high" if match["score"] >= 85 else "medium" if match["score"] >= 60 else "info",
            "title": match["title"], "summary": match["summary"], "evidence": match["evidence"],
            "score": match["score"], "source": source, "as_of": quote.get("as_of"),
            "target_time": next_forecast.get("target_time") if match["trigger"] == "forward_forecast" else None, "horizon_label": forecast.get("horizon_label") if match["trigger"] == "forward_forecast" else None,
            "model_method": forecast.get("method"), "signal_version": 2,
        })
    return {
        "asset": asset,
        "quote": quote,
        "technical": {"score": technical.get("score"), "stance": technical.get("stance"), "relative_volume": technical.get("relative_volume")},
        "forecast": {"status": forecast.get("status"), "predicted_return_pct": next_forecast.get("predicted_return_pct"), "predicted_price": next_forecast.get("predicted_price"), "target_time": next_forecast.get("target_time"), "horizon_label": forecast.get("horizon_label"), "confidence": (forecast.get("confidence") or {}).get("score"), "publishable": next_forecast.get("publishable"), "profile": (forecast.get("ensemble") or {}).get("profile")},
        "market_sentiment": {"score": sentiment.get("score"), "label": sentiment.get("label"), "confidence": sentiment.get("confidence")},
        "status": status,
        "matches": matches,
        "alerts": alerts,
        "source": {"name": source, "as_of": quote.get("as_of"), "feed": quote.get("feed")},
    }
