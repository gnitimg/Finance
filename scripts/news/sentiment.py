from __future__ import annotations

POSITIVE = {"增长": 1.0, "上涨": 0.8, "突破": 0.8, "盈利": 1.0, "回购": 0.7, "增持": 0.8, "中标": 0.7, "record": 0.8, "growth": 0.8, "profit": 0.9, "beat": 0.8, "upgrade": 0.8, "surge": 0.8, "gain": 0.5}
NEGATIVE = {"下跌": 0.7, "亏损": 1.0, "减持": 0.8, "违约": 1.0, "调查": 0.7, "处罚": 0.8, "风险": 0.4, "跌破": 0.7, "loss": 0.9, "miss": 0.8, "downgrade": 0.8, "probe": 0.6, "fraud": 1.0, "plunge": 0.9, "risk": 0.4}
NEGATORS = ("并未", "没有", "不及", "未能", "难以", "无望", "缺乏", "取消", "推迟", "放缓", "not ", "no longer", "fails to", "unlikely", "barely")
AMPLIFIERS = ("大幅", "暴跌", "飙升", "暴涨", "创纪录", "急剧", "锐减", "plunges", "soars", "surges", "sharply", "record-high")
DETERRENTS = ("略有", "小幅", "微涨", "微跌", "轻微", "slightly", "marginally", "modest")
ENTITY_MISS_WEIGHT = 0.35


def _negated(text: str, position: int) -> bool:
    window = text[max(0, position - 4):position]
    if any(negator in window for negator in NEGATORS):
        return True
    english_window = text[max(0, position - 14):position]
    return any(negator in english_window for negator in NEGATORS)


def _relevance(text: str, aliases: list[str]) -> float:
    if not aliases:
        return 1.0
    return 1.0 if any(alias in text for alias in aliases) else ENTITY_MISS_WEIGHT


def _degree(text: str) -> float:
    if any(word in text for word in AMPLIFIERS):
        return 1.4
    if any(word in text for word in DETERRENTS):
        return 0.6
    return 1.0


def analyze(items: list[dict], entity: str | None = None) -> dict:
    aliases = []
    clean = (entity or "").strip()
    if clean:
        aliases.append(clean.lower())
        first_word = clean.split()[0].lower()
        if first_word not in aliases:
            aliases.append(first_word)
    evidence = []
    total = 0.0
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        item_score = 0.0
        hits = []
        for word, weight in POSITIVE.items():
            position = text.find(word.lower())
            if position < 0:
                continue
            signed = -weight * 0.9 if _negated(text, position) else weight
            item_score += signed
            hits.append({"term": word, "direction": "negative" if signed < 0 else "positive", "weight": round(signed, 3)})
        for word, weight in NEGATIVE.items():
            position = text.find(word.lower())
            if position < 0:
                continue
            signed = -weight * 0.9 if _negated(text, position) else -weight
            item_score += signed
            hits.append({"term": word, "direction": "negative" if signed < 0 else "positive", "weight": round(signed, 3)})
        if hits:
            relevance = _relevance(text, aliases)
            normalized = max(-1.0, min(1.0, item_score / max(1.0, len(hits)))) * _degree(text) * relevance
            total += normalized
            entry = {"title": item.get("title"), "score": round(normalized, 4), "hits": hits[:5]}
            if relevance < 1.0:
                entry["entity_miss"] = True
            evidence.append(entry)
    score = total / len(evidence) if evidence else 0.0
    sources = len({item.get("source") for item in items if item.get("source")})
    coverage = min(1.0, len(evidence) / 8)
    source_factor = min(1.0, sources / 2)
    confidence = 100 * (0.65 * coverage + 0.35 * source_factor) if items else 0.0
    label = "positive" if score >= 0.18 else "negative" if score <= -0.18 else "neutral"
    return {"score": round(score, 4), "label": label, "confidence": confidence, "confidence_kind": "evidence_quality_not_price_probability", "evidence_count": len(evidence), "article_count": len(items), "source_count": sources, "evidence": evidence[:8], "method": "lexicon_v2_negation_degree_entity"}
