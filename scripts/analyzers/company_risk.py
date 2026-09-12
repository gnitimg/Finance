from __future__ import annotations

import math
import re
import statistics
from datetime import datetime, timezone


RISK_CATEGORIES = (
    {
        "key": "negative_public_opinion",
        "label": "负面舆情",
        "description": "事故、诉讼、违约、抵制、裁员及其他可能冲击经营或声誉的公开消息。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("负面舆情", "舆情危机", "重大事故", "安全事故", "诉讼", "被诉", "违约", "爆雷", "抵制", "裁员", "scandal", "lawsuit", "boycott", "defaulted"),
    },
    {
        "key": "equity_pledge",
        "label": "股权质押",
        "description": "控股股东或重要股东质押、补充质押及质押风险。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("股权质押", "股份质押", "补充质押", "质押股份", "pledged shares", "share pledge"),
    },
    {
        "key": "investigation",
        "label": "立案调查",
        "description": "公司、实控人或董监高被监管或司法机关立案调查。",
        "severity": "high",
        "coverage": "headline",
        "terms": ("立案调查", "立案侦查", "被立案", "接受调查", "under investigation", "formal investigation"),
    },
    {
        "key": "violation_penalty",
        "label": "违规处罚",
        "description": "行政处罚、纪律处分、违规认定及重大罚款。",
        "severity": "high",
        "coverage": "headline",
        "terms": ("违规处罚", "行政处罚", "纪律处分", "监管处罚", "责令改正", "收到罚单", "被罚", "罚款", "regulatory penalty", "fined"),
    },
    {
        "key": "regulatory_inquiry",
        "label": "监管问询",
        "description": "交易所问询函、关注函、监管函及重大信息披露追问。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("监管问询", "问询函", "关注函", "监管函", "交易所问询", "regulatory inquiry", "exchange inquiry"),
    },
    {
        "key": "negative_rating",
        "label": "负面评级",
        "description": "评级下调、负面展望、卖出评级或信用等级恶化。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("评级下调", "下调评级", "负面展望", "列入负面", "卖出评级", "信用等级下调", "downgrade", "negative outlook", "sell rating"),
    },
    {
        "key": "lockup_expiry",
        "label": "限售解禁",
        "description": "限售股份即将或已经解禁形成的潜在供给压力。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("限售解禁", "限售股解禁", "解除限售", "解禁股份", "lock-up expiry", "lockup expiration"),
    },
    {
        "key": "shareholder_reduction",
        "label": "股东减持",
        "description": "重要股东、董监高减持计划或已实施减持。",
        "severity": "medium",
        "coverage": "headline",
        "terms": ("股东减持", "减持计划", "拟减持", "完成减持", "高管减持", "shareholder selling", "insider selling", "stake reduction"),
    },
    {
        "key": "large_order_outflow",
        "label": "大单资金流出",
        "description": "以公开主力资金净流序列作为大单方向的代理，不等同于完整逐笔成交。",
        "severity": "medium",
        "coverage": "flow",
        "terms": ("大单资金流出", "主力资金流出", "主力净流出", "资金大幅流出", "institutional outflow"),
    },
    {
        "key": "earnings_risk",
        "label": "业绩风险",
        "description": "预亏、业绩下修、盈利显著下降及经营目标落空。",
        "severity": "high",
        "coverage": "headline",
        "terms": ("业绩预亏", "业绩下修", "业绩下降", "利润大降", "净利润下降", "由盈转亏", "预计亏损", "业绩不及预期", "profit warning", "earnings miss", "guidance cut"),
        "patterns": (r"(?:净利润|利润|营收).{0,24}(?:下降|下滑|减少|亏损)", r"(?:revenue|profit|earnings).{0,24}(?:fell|declined|dropped|loss)"),
    },
    {
        "key": "audit_opinion",
        "label": "审计意见",
        "description": "保留、否定、无法表示意见或持续经营重大不确定性。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("保留意见", "否定意见", "无法表示意见", "非标准审计意见", "持续经营重大不确定性", "qualified opinion", "adverse opinion", "going concern"),
    },
    {
        "key": "financial_analysis",
        "label": "财务分析",
        "description": "公开消息中披露的财务造假、指标恶化或资产负债异常。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("财务造假", "财务异常", "虚增收入", "虚增利润", "财务指标恶化", "资产负债率攀升", "会计差错", "financial irregularities", "accounting fraud", "balance sheet deterioration"),
    },
    {
        "key": "st_risk",
        "label": "ST 风险",
        "description": "退市风险警示、其他风险警示及可能触发 ST 的事项。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("st风险", "*st", "实施st", "风险警示", "退市风险", "可能被st", "delisting risk", "delisting warning"),
    },
    {
        "key": "goodwill",
        "label": "商誉风险",
        "description": "高商誉、商誉减值测试或大额商誉减值。",
        "severity": "medium",
        "coverage": "financial_headline",
        "terms": ("商誉减值", "商誉风险", "大额商誉", "goodwill impairment", "goodwill write-down"),
    },
    {
        "key": "deposit_loan_high",
        "label": "存贷双高",
        "description": "货币资金与有息负债同时偏高等需要核验的财务结构。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("存贷双高", "货币资金与负债双高", "现金与债务双高", "high cash and debt"),
    },
    {
        "key": "cashflow_interruption",
        "label": "现金流中断风险",
        "description": "资金链紧张、流动性枯竭、债务逾期或持续经营承压。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("现金流中断", "资金链断裂", "资金链紧张", "流动性危机", "债务逾期", "无法偿债", "cash flow crisis", "liquidity crisis", "debt default"),
    },
    {
        "key": "inventory_impairment",
        "label": "存货减值",
        "description": "存货跌价准备、存货减值或异常积压。",
        "severity": "medium",
        "coverage": "financial_headline",
        "terms": ("存货减值", "存货跌价", "存货积压", "inventory impairment", "inventory write-down"),
    },
    {
        "key": "receivables_bad_debt",
        "label": "应收账款坏账",
        "description": "应收账款回收恶化、坏账准备或重大客户欠款。",
        "severity": "medium",
        "coverage": "financial_headline",
        "terms": ("应收账款坏账", "坏账准备", "应收账款逾期", "应收款减值", "bad debt", "receivables impairment"),
    },
    {
        "key": "financial_distress",
        "label": "财务困境",
        "description": "资不抵债、债务重组、破产重整或持续经营能力显著受损。",
        "severity": "high",
        "coverage": "financial_headline",
        "terms": ("财务困境", "资不抵债", "债务重组", "破产重整", "申请破产", "偿债困难", "financial distress", "insolvency", "bankruptcy", "debt restructuring"),
    },
)

_NEGATED_PHRASES = (
    "不存在退市风险",
    "不存在资金链断裂",
    "不存在债务逾期",
    "未被立案",
    "未受到行政处罚",
    "免于行政处罚",
    "撤销立案",
    "终止调查",
    "解除股份质押",
    "解除股权质押",
    "终止减持计划",
    "取消减持计划",
    "评级上调",
    "撤销风险警示",
    "audit opinion withdrawn",
    "investigation closed",
)

_PROVIDER_LABELS = {"eastmoney": "东方财富", "gdelt": "GDELT", "yahoo": "Yahoo Finance"}


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _plain(value) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or "")).casefold()


def _is_negated(text: str, term: str) -> bool:
    position = text.find(term.casefold())
    if position < 0:
        return False
    window = text[max(0, position - 12): position + len(term) + 14]
    return any(phrase.casefold() in window for phrase in _NEGATED_PHRASES)


def _contains_term(text: str, term: str) -> bool:
    folded = term.casefold()
    if re.fullmatch(r"[a-z][a-z '\-]*", folded):
        return re.search(rf"(?<![a-z]){re.escape(folded)}(?![a-z])", text) is not None
    return folded in text


def _headline_evidence(items: list[dict], category: dict) -> list[dict]:
    evidence = []
    seen = set()
    for item in items:
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        text = _plain(f"{title} {summary}")
        matched = next((term for term in category["terms"] if _contains_term(text, term) and not _is_negated(text, term)), None)
        if not matched:
            matched = next((f"pattern:{pattern}" for pattern in category.get("patterns") or () if re.search(pattern, text, flags=re.IGNORECASE)), None)
        if not matched:
            continue
        key = str(item.get("url") or title)
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "type": "headline",
            "title": title,
            "source": item.get("source") or "关联消息源",
            "published_at": item.get("published_at"),
            "url": item.get("url"),
            "matched_term": matched,
        })
        if len(evidence) >= 3:
            break
    return evidence


def _flow_evidence(rows: list[dict] | None) -> tuple[bool, dict | None, dict]:
    usable = []
    for row in rows or []:
        value = _number(row.get("main_net"))
        if value is not None:
            usable.append((row, value))
    if not usable:
        return False, None, {"available": False, "observations": 0}

    latest_row, latest = usable[-1]
    baseline = [value for _row, value in usable[-21:-1]]
    median_abs = statistics.median(abs(value) for value in baseline) if baseline else 0.0
    zscore = None
    if len(baseline) >= 5:
        deviation = statistics.stdev(baseline)
        if deviation > 0:
            zscore = (latest - statistics.mean(baseline)) / deviation
    material = latest < 0 and (
        (zscore is not None and zscore <= -1.0)
        or (len(baseline) >= 3 and median_abs > 0 and abs(latest) >= 1.5 * median_abs)
    )
    meta = {
        "available": True,
        "observations": len(usable),
        "latest_main_net": latest,
        "zscore_20": zscore,
        "as_of": latest_row.get("date"),
        "source": "东方财富主力资金流",
        "proxy": "主力净流入作为大单方向代理",
    }
    if not material:
        return True, None, meta
    sign = "净流出" if latest < 0 else "净流入"
    evidence = {
        "type": "fund_flow",
        "title": f"主力资金显著{sign}（大单方向代理）",
        "source": "东方财富主力资金流",
        "published_at": latest_row.get("date"),
        "url": None,
        "matched_term": "主力净流出",
        "value": latest,
        "zscore_20": zscore,
    }
    return True, evidence, meta


def analyze(
    items: list[dict] | None,
    *,
    market: str | None = None,
    entity: str | None = None,
    flow: list[dict] | None = None,
    news_providers: list[str] | None = None,
    as_of: str | None = None,
    structured: dict | None = None,
) -> dict:
    """Screen public evidence without interpreting absence as safety.

    Headline matches are deterministic keyword evidence.  Structured
    datacenter findings (pledge ratio, lockup schedule, earnings forecast)
    upgrade balance-sheet categories from source-limited to covered; flagged
    findings become detected with the reported figure as evidence.
    """
    market = str(market or "").lower()
    applicable = market in {"cn", "hk", "us", "auto", ""}
    items = list(items or [])
    inferred_providers = sorted({str(item.get("source")) for item in items if item.get("source")})
    providers = sorted({str(name) for name in (news_providers or inferred_providers) if name})
    news_available = bool(providers)
    flow_available, flow_hit, flow_meta = _flow_evidence(flow)
    categories = []

    structured_findings = (structured or {}).get("findings") if isinstance(structured, dict) else None
    structured_available = structured_findings is not None
    for spec in RISK_CATEGORIES:
        evidence = _headline_evidence(items, spec) if applicable and news_available else []
        if spec["key"] == "large_order_outflow" and flow_hit:
            evidence.insert(0, flow_hit)
        structured_note = None
        finding = (structured_findings or {}).get(spec["key"])
        if applicable and finding:
            # Structured data outranks headline keyword matches for the
            # categories it actually covers.
            evidence = [item for item in evidence if item.get("type") == "headline"][:1] if evidence else []
            if finding.get("detected"):
                evidence.insert(0, {"type": "structured", "title": finding.get("detail"), "source": "东方财富数据中心"})
            elif finding.get("detail"):
                structured_note = {"type": "structured_note", "title": finding.get("detail"), "source": "东方财富数据中心"}
        if not applicable:
            status = "not_applicable"
        elif finding and finding.get("detected"):
            status = "detected"
        elif evidence:
            status = "detected"
        elif spec["coverage"] == "flow":
            status = "no_evidence" if flow_available or news_available else "unavailable"
        elif not news_available and not structured_available:
            status = "unavailable"
        elif spec["coverage"] == "financial_headline":
            # Structured coverage turns a source-limited category into an
            # explicit no-evidence verdict; without it the honest label stays.
            status = "no_evidence" if structured_available and spec["key"] in (structured_findings or {}) else "source_limited"
        else:
            status = "no_evidence"
        if structured_note:
            evidence.append(structured_note)
        severity_to_level = {"high": "high", "medium": "medium", "info": "low"}
        level = None
        if status == "detected":
            level = (finding or {}).get("level") or severity_to_level.get(spec["severity"], "medium")
        categories.append({
            "key": spec["key"],
            "label": spec["label"],
            "description": spec["description"],
            "severity": spec["severity"],
            "coverage": spec["coverage"],
            "status": status,
            "level": level,
            "evidence_count": len(evidence),
            "evidence": evidence,
        })

    detected = [category for category in categories if category["status"] == "detected"]
    severity_weight = {"info": 35.0, "medium": 60.0, "high": 82.0}
    priority_score = 0.0
    if detected:
        priority_score = min(100.0, max(severity_weight[item["severity"]] for item in detected) + min(15.0, (len(detected) - 1) * 3.0))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    sources = [{"name": _PROVIDER_LABELS.get(provider.casefold(), provider), "type": "related_headlines"} for provider in providers]
    if flow_available:
        sources.append({"name": "东方财富主力资金流", "type": "fund_flow", "as_of": flow_meta.get("as_of")})
    if structured_available:
        for source in (structured or {}).get("sources") or []:
            sources.append({"name": source, "type": "structured_reports"})
    covered_count = sum(category["status"] in {"detected", "no_evidence"} for category in categories)
    limited_count = sum(category["status"] == "source_limited" for category in categories)
    unavailable_count = sum(category["status"] == "unavailable" for category in categories)
    return {
        "status": "ready" if applicable and (news_available or flow_available) else "source_unavailable" if applicable else "not_applicable",
        "applicable": applicable,
        "entity": entity,
        "priority_score": round(priority_score, 1),
        "severity": max((item["severity"] for item in detected), key=lambda value: severity_weight[value], default="none"),
        "detected_count": len(detected),
        "category_count": len(categories),
        "covered_count": covered_count,
        "limited_count": limited_count,
        "unavailable_count": unavailable_count,
        "detected": detected,
        "categories": categories,
        "sources": sources,
        "flow": flow_meta,
        "as_of": as_of or generated_at,
        "method": "deterministic_public_evidence_screen_v2",
        "disclaimer": "未命中仅表示当前已接入来源没有发现证据，不表示风险不存在；结构化报表已覆盖质押、解禁与业绩预告，其余财报类项目仍局限于来源受限。",
    }
