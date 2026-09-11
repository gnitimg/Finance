from __future__ import annotations

import re

L2_PATTERNS = [r"深入", r"深度", r"比较", r"哪个.{0,8}(更好|风险)", r"如果", r"假如", r"情景", r"值不值得", r"是否值得", r"该不该", r"风险收益", r"trade.?off", r"scenario", r"compare", r"deep analysis"]
L1_PATTERNS = [r"分析", r"技术面", r"RSI", r"MACD", r"均线", r"MA\d+", r"支撑", r"阻力", r"趋势", r"波动", r"风险", r"预测", r"模型", r"analy", r"technical", r"forecast"]
CAUSAL_PATTERNS = [r"为什么", r"原因", r"怎么回事", r"why"]


def classify(query: str, asset_count: int = 1, signal_conflict: bool = False) -> dict:
    text = query.strip()
    reasons = []
    causal = any(re.search(pattern, text, re.I) for pattern in CAUSAL_PATTERNS)
    if asset_count > 1:
        reasons.append("multi_asset_comparison")
    for pattern in L2_PATTERNS:
        if re.search(pattern, text, re.I):
            reasons.append("explicit_reasoning_intent")
            break
    if signal_conflict:
        reasons.append("material_signal_conflict")
    if reasons:
        level = "L2"
    elif any(re.search(pattern, text, re.I) for pattern in L1_PATTERNS):
        level = "L1"
        reasons.append("deterministic_analysis")
    else:
        level = "L0"
        reasons.append("fact_query")
    return {
        "level": level,
        "specialist_recommended": level == "L2" and not causal,
        "reason": reasons[0],
        "reasons": reasons,
        "causal_reasoning": causal,
        "causal_data_available": False,
    }
