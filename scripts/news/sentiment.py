from __future__ import annotations

POSITIVE = {"增长": 1.0, "上涨": 0.8, "突破": 0.8, "盈利": 1.0, "回购": 0.7, "增持": 0.8, "中标": 0.7, "record": 0.8, "growth": 0.8, "profit": 0.9, "beat": 0.8, "upgrade": 0.8, "surge": 0.8, "gain": 0.5}
NEGATIVE = {"下跌": 0.7, "亏损": 1.0, "减持": 0.8, "违约": 1.0, "调查": 0.7, "处罚": 0.8, "风险": 0.4, "跌破": 0.7, "loss": 0.9, "miss": 0.8, "downgrade": 0.8, "probe": 0.6, "fraud": 1.0, "plunge": 0.9, "risk": 0.4}


def analyze(items: list[dict]) -> dict:
    evidence = []
    total = 0.0
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        item_score = 0.0
        hits = []
        for word, weight in POSITIVE.items():
            if word.lower() in text:
                item_score += weight
                hits.append({"term": word, "direction": "positive", "weight": weight})
        for word, weight in NEGATIVE.items():
            if word.lower() in text:
                item_score -= weight
                hits.append({"term": word, "direction": "negative", "weight": weight})
        if hits:
            normalized = max(-1.0, min(1.0, item_score / max(1.0, len(hits))))
            total += normalized
            evidence.append({"title": item.get("title"), "score": normalized, "hits": hits[:5]})
    score = total / len(evidence) if evidence else 0.0
    sources = len({item.get("source") for item in items if item.get("source")})
    coverage = min(1.0, len(evidence) / 8)
    source_factor = min(1.0, sources / 2)
    confidence = 100 * (0.65 * coverage + 0.35 * source_factor) if items else 0.0
    label = "positive" if score >= 0.18 else "negative" if score <= -0.18 else "neutral"
    return {"score": score, "label": label, "confidence": confidence, "confidence_kind": "evidence_quality_not_price_probability", "evidence_count": len(evidence), "article_count": len(items), "source_count": sources, "evidence": evidence[:8]}
