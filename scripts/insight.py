from __future__ import annotations

import json
import os
import re
import urllib.request

from .model_registry import enabled_model
from .models import FinanceError, clean_json

MAX_ROUNDS = 3
MAX_TOOL_PAYLOAD = 48_000
HISTORY_WINDOW = 60


SYSTEM_PROMPT = """你是金融数据解读助手,运行在 GNITIMG Finance 的只读安全框架内。
规则:
1. 你只能通过工具获取数据,禁止编造任何数字、事件或结论;工具没给的信息就说"数据未提供"。
2. 你只能做解读、归纳与风险提示;禁止给出任何买卖指令、目标价、收益承诺。
3. 数据均来自 finance skill 只读接口,已带来源与时间戳;引用时保留不确定性。
4. 最终回复必须是严格 JSON:{"headline": "一句话结论", "reading": "150字以内的解读", "risks": ["风险点1", "风险点2"], "uncertainty": "主要不确定性来源"}
"""

TOOLS_SPEC = [
    {"type": "function", "function": {"name": "quote", "description": "获取某标的当前报价与来源时间戳", "parameters": {"type": "object", "properties": {"market": {"type": "string"}, "symbol": {"type": "string"}}, "required": ["market", "symbol"]}}},
    {"type": "function", "function": {"name": "analyze", "description": "获取某标的完整分析:技术指标、市场情绪、模型前瞻(历史条数受限)", "parameters": {"type": "object", "properties": {"market": {"type": "string"}, "symbol": {"type": "string"}, "range": {"type": "string", "enum": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]}, "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1d"]}}, "required": ["market", "symbol"]}}},
    {"type": "function", "function": {"name": "news", "description": "获取某标的的关联新闻与风险筛查摘要", "parameters": {"type": "object", "properties": {"market": {"type": "string"}, "symbol": {"type": "string"}}, "required": ["market", "symbol"]}}},
    {"type": "function", "function": {"name": "health", "description": "获取数据源健康状态", "parameters": {"type": "object", "properties": {}}}},
]


def _clip_history(bars: list) -> list:
    return [
        {"time": bar.get("time"), "close": bar.get("close"), "volume": bar.get("volume")}
        for bar in (bars or [])[-HISTORY_WINDOW:]
    ]


def _clip_analysis(data: dict) -> dict:
    forecast = data.get("ml_forecast") or {}
    return {
        "asset": data.get("asset"),
        "quote": data.get("quote"),
        "cache": data.get("cache"),
        "technical": {k: data.get("technical", {}).get(k) for k in ("status", "stance", "score", "rsi14", "macd", "atr_pct", "relative_volume", "signals")},
        "market_sentiment": {k: data.get("market_sentiment", {}).get(k) for k in ("score", "label", "regime", "factors")},
        "ml_forecast": {
            "status": forecast.get("status"), "horizon_label": forecast.get("horizon_label"),
            "evaluation": forecast.get("evaluation"), "confidence": forecast.get("confidence"),
            "next_forecast": forecast.get("next_forecast"), "ensemble": forecast.get("ensemble"),
        },
        "company_risk": {
            "priority_score": (data.get("company_risk") or {}).get("priority_score"),
            "detected": (data.get("company_risk") or {}).get("detected"),
        },
        "history_tail": _clip_history(data.get("history")),
        "sector": data.get("sector"),
    }


def _execute_tool(name: str, arguments: dict, market: str, symbol: str) -> dict:
    from .finance import analyze_asset, news_asset, quote_asset, health as health_report
    if name == "quote":
        return quote_asset(arguments.get("market") or market, arguments.get("symbol") or symbol)["data"]
    if name == "analyze":
        range_name = arguments.get("range") or "3mo"
        interval = arguments.get("interval") or "1d"
        try:
            result = analyze_asset(arguments.get("market") or market, arguments.get("symbol") or symbol, range_name, interval)
        except (ValueError, FinanceError):
            # The model may invent a range/interval pair; fall back instead of
            # aborting the whole interpretation.
            result = analyze_asset(arguments.get("market") or market, arguments.get("symbol") or symbol)
        return _clip_analysis(result["data"])
    if name == "news":
        return news_asset(arguments.get("market") or market, arguments.get("symbol") or symbol)
    if name == "health":
        return health_report()["data"]
    raise FinanceError("TOOL_UNKNOWN", f"未知工具 {name}", "insight")


def _call_llm(endpoint: str, model: str, key: str, messages: list) -> dict:
    payload = {"model": model, "temperature": 0.2, "max_tokens": 900, "messages": messages, "tools": TOOLS_SPEC}
    if "qwen3" in model.lower():
        # Thinking mode on free Qwen3 routinely exceeds interactive budgets.
        payload["enable_thinking"] = False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "gnitimg-finance/1.0"})
    timeout = max(15, min(int(os.getenv("FINANCE_INSIGHT_TIMEOUT", "60")), 120))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise FinanceError("LLM_TIMEOUT", f"LLM 响应超过 {timeout}s;可在模型配置中更换更快的模型", "insight") from exc
    except urllib.error.URLError as exc:
        raise FinanceError("LLM_UNAVAILABLE", f"LLM 服务不可达: {exc.reason}", "insight") from exc


def _sanitize(value):
    from .specialist.privacy import sanitize
    return sanitize(value)


def _parse_final(content: str) -> dict:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise FinanceError("INSIGHT_INVALID", "LLM 解读返回格式不正确", "insight")
    parsed = json.loads(text[start:end + 1])
    return {
        "headline": str(parsed.get("headline") or "")[:120],
        "reading": str(parsed.get("reading") or "")[:400],
        "risks": [str(r)[:120] for r in (parsed.get("risks") or [])[:5]],
        "uncertainty": str(parsed.get("uncertainty") or "")[:200],
    }


def generate_insight(market: str, symbol: str, question: str | None = None) -> dict:
    """Bounded tool-loop: the configured chat model may call read-only finance
    tools (≤3 rounds, clipped payloads, sanitized) before producing a JSON
    interpretation.  Without a configured chat model this raises and callers
    degrade to the deterministic panels."""
    slot = enabled_model("chat")
    if not slot:
        raise FinanceError("NOT_CONFIGURED", "未配置对话模型;AI 解读保持关闭", "insight")
    endpoint = f"{str(slot['base_url']).rstrip('/')}/chat/completions"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({"market": market, "symbol": symbol, "question": question or "解读当前行情、模型前瞻与风险证据"}, ensure_ascii=False)},
    ]
    tool_trace = []
    parsed = None
    for _round in range(MAX_ROUNDS):
        payload = _call_llm(endpoint, str(slot["model"]), str(slot["api_key"]), _sanitize(messages))
        message = (payload.get("choices") or [{}])[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            parsed = _parse_final(str(message.get("content") or ""))
            break
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls[:4]:
            name = call.get("function", {}).get("name")
            try:
                arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            try:
                result = _execute_tool(str(name), arguments, market, symbol)
                content = json.dumps(clean_json(result), ensure_ascii=False)[:MAX_TOOL_PAYLOAD]
            except FinanceError as exc:
                content = json.dumps({"error": exc.message}, ensure_ascii=False)
            tool_trace.append({"tool": name, "arguments": arguments})
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": content})
    if parsed is None:
        raise FinanceError("INSIGHT_INCOMPLETE", "工具轮次已达上限,模型未给出最终解读", "insight")
    parsed["tools_used"] = [item["tool"] for item in tool_trace]
    parsed["model"] = str(slot["model"])
    parsed["grounded"] = True
    parsed["disclaimer"] = "本节内容由大语言模型生成,具有不确定性,请谨慎参考;数据来自 finance skill 只读接口。"
    return parsed
