from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from ..cache import CACHE
from ..config import env_bool, read_json
from ..http_client import request_json
from ..models import FinanceError
from ..model_registry import enabled_model

SYSTEM_PROMPT = (
    "You are a financial news sentiment scorer. For each headline, judge the "
    "sentiment toward the mentioned company or asset from an investor's point "
    "of view, in [-1, 1]. Handle negation and context; do not reward mere "
    "keyword matches. Reply with strict JSON only: [{\"i\": <index>, \"s\": <score>}]."
)
BATCH_SIZE = 10
SCORE_TTL = 21_600
FAILURE_THRESHOLD = 2
COOLDOWN_SECONDS = 600
_BREAKER_KEY = "news:llm:cooldown"
_FAILURES_KEY = "news:llm:failures"


def enabled() -> bool:
    # Explicit env switch, or any enabled user chat model in the registry.
    if env_bool("FINANCE_SENTIMENT_LLM_ENABLED", False):
        return True
    try:
        return enabled_model("chat") is not None
    except Exception:
        return False


def _cooldown_until() -> float:
    marker, _ = CACHE.get(_BREAKER_KEY, 7200)
    return float((marker or {}).get("until") or 0)


def _provider_policy() -> tuple[str, str, str]:
    # A user-configured chat model (settings pane) outranks the allowlist.
    custom = enabled_model("chat")
    if custom:
        return f"{str(custom['base_url']).rstrip('/')}/chat/completions", str(custom["model"]), str(custom["api_key"])
    provider = os.getenv("FINANCE_SENTIMENT_LLM_PROVIDER", "opencode_zen").strip()
    policy = read_json("model-policy.json").get("providers", {}).get(provider)
    if not policy:
        raise FinanceError("SPECIALIST_PROVIDER", f"Unsupported sentiment provider: {provider}", "llm-sentiment")
    model = os.getenv("FINANCE_SENTIMENT_LLM_MODEL", "").strip() or (policy.get("allowed_models") or [None])[0]
    if not model:
        raise FinanceError("MODEL_POLICY", "No sentiment model configured", "llm-sentiment")
    key_name = "GOOGLE_API_KEY" if provider == "google" else "OPENCODE_ZEN_API_KEY"
    key = os.getenv(key_name, "").strip()
    if not key:
        raise FinanceError("MISSING_API_KEY", f"{key_name} is not configured", "llm-sentiment")
    return policy["endpoint"], model, key


def _cache_key(item: dict) -> str:
    digest = hashlib.sha1(str(item.get("url") or item.get("title") or "").encode("utf-8")).hexdigest()[:20]
    return f"llmsent:{digest}"


def _score_batch(endpoint: str, model: str, key: str, headlines: list[str]) -> list[float]:
    body = json.dumps({
        "model": model,
        "temperature": 0.0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps([{"i": index, "title": line} for index, line in enumerate(headlines)], ensure_ascii=False)},
        ],
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "gnitimg-finance/1.0"})
    timeout = max(5, min(int(os.getenv("FINANCE_SENTIMENT_LLM_TIMEOUT", "14")), 30))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("```")[1].removeprefix("json").strip()
    start, end = content.find("["), content.rfind("]")
    if start < 0 or end <= start:
        raise FinanceError("INVALID_SCHEMA", "LLM sentiment returned no list", "llm-sentiment")
    parsed = json.loads(content[start:end + 1])
    scores = [0.0] * len(headlines)
    for entry in parsed:
        try:
            index = int(entry["i"])
            scores[index] = max(-1.0, min(1.0, float(entry["s"])))
        except (KeyError, ValueError, IndexError, TypeError):
            continue
    return scores


def _record_failure() -> None:
    failures, _ = CACHE.get(_FAILURES_KEY, 86_400) or ({"count": 0}, {})
    count = int((failures or {}).get("count") or 0) + 1
    CACHE.set(_FAILURES_KEY, {"count": count})
    if count >= FAILURE_THRESHOLD:
        CACHE.set(_BREAKER_KEY, {"until": time.time() + COOLDOWN_SECONDS})
        CACHE.set(_FAILURES_KEY, {"count": 0})


def score_items(items: list[dict]) -> dict[str, float]:
    """Return {url: score} for every scoreable item, using cache first.

    Each article is scored once and cached for six hours, so the LLM is called
    only for genuinely new headlines.
    """
    if not enabled() or not items:
        return {}
    if time.time() < _cooldown_until():
        raise FinanceError("PROVIDER_COOLDOWN", "LLM sentiment skipped during failure cooldown", "llm-sentiment")
    endpoint, model, key = _provider_policy()
    scores: dict[str, float] = {}
    pending: list[dict] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        cached, _meta = CACHE.get(_cache_key(item), SCORE_TTL)
        if cached is not None and isinstance(cached, dict) and "score" in cached:
            scores[url] = float(cached["score"])
        else:
            pending.append(item)
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start:batch_start + BATCH_SIZE]
        headlines = [f"{item.get('title', '')}"[:220] for item in batch]
        try:
            batch_scores = _score_batch(endpoint, model, key, headlines)
        except FinanceError:
            _record_failure()
            raise
        except Exception as exc:
            _record_failure()
            raise FinanceError("SPECIALIST_UNAVAILABLE", f"LLM sentiment failed: {type(exc).__name__}", "llm-sentiment") from exc
        for item, score in zip(batch, batch_scores):
            CACHE.set(_cache_key(item), {"score": round(score, 4)})
            scores[str(item.get("url"))] = float(score)
    if scores:
        CACHE.set(_FAILURES_KEY, {"count": 0})
        CACHE.set(_BREAKER_KEY, {"until": 0})
    return scores


def apply(sentiment: dict, items: list[dict], llm_scores: dict[str, float], method: str = "lexicon_v2+llm") -> dict:
    """Blend model per-article scores with the lexicon aggregate (0.6 / 0.4)."""
    if not llm_scores:
        return sentiment
    title_to_url = {}
    for item in items:
        if item.get("url"):
            title_to_url[str(item.get("title"))] = str(item.get("url"))
    adjusted = []
    llm_used = 0
    for entry in sentiment.get("evidence", []):
        url = title_to_url.get(str(entry.get("title")))
        lexicon = float(entry.get("score") or 0)
        if url is not None and url in llm_scores:
            llm_used += 1
            merged = 0.6 * llm_scores[url] + 0.4 * lexicon
        else:
            merged = lexicon
        adjusted.append({**entry, "score": round(merged, 4), "llm": url in llm_scores if url else False})
    if not adjusted:
        adjusted = sentiment.get("evidence", [])
    total = sum(entry["score"] for entry in adjusted)
    score = total / len(adjusted) if adjusted else 0.0
    label = "positive" if score >= 0.18 else "negative" if score <= -0.18 else "neutral"
    return {
        **sentiment,
        "score": round(score, 4),
        "label": label,
        "evidence": adjusted,
        "llm_scored": llm_used,
        "method": method,
    }


EVENT_PROMPT = (
    "你是财经事件评估器。根据给出的标题判断近期事件对公司股价数日维度综合影响。"
    "只依据给定标题,禁止编造;没有明确方向就给接近 0 的值。"
    "回复严格 JSON:{\"direction\": <-1..1>, \"confidence\": <0..1>, \"events\": [\"事件\", ...]}，events 最多 3 条。"
)


def event_assessment(items: list[dict], entity: str | None = None) -> dict | None:
    """LLM assessment of recent event direction for the forecast live context.

    Cached for 30 minutes per entity per day; returns None whenever the AI
    path is off, so the deterministic forecast never depends on it.
    """
    if not enabled() or not items:
        return None
    if time.time() < _cooldown_until():
        return None
    endpoint, model, key = _provider_policy()
    entity_key = (entity or "x").strip() or "x"
    today = datetime.now(timezone.utc).date().isoformat()
    cache_key = f"aievents:{entity_key}:{today}"
    cached, _meta = CACHE.get(cache_key, 1800)
    if cached is not None and isinstance(cached, dict):
        return cached
    headlines = [f"{index + 1}. {str(item.get('title') or '')[:160]}" for index, item in enumerate(items[:10])]
    payload = {
        "model": model, "temperature": 0.1, "max_tokens": 300,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": EVENT_PROMPT},
            {"role": "user", "content": json.dumps({"entity": entity_key, "headlines": headlines}, ensure_ascii=False)},
        ],
    }
    if "qwen3" in model.lower():
        payload["enable_thinking"] = False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "gnitimg-finance/1.0"})
    timeout = max(10, min(int(os.getenv("FINANCE_SENTIMENT_LLM_TIMEOUT", "30")), 60))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1].removeprefix("json").strip()
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        parsed = json.loads(content[start:end + 1])
    except Exception as exc:
        _record_failure()
        raise FinanceError("SPECIALIST_UNAVAILABLE", f"事件评估失败: {type(exc).__name__}", "llm-events") from exc
    direction = max(-1.0, min(1.0, float(parsed.get("direction") or 0)))
    confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0)))
    events = [str(e)[:80] for e in (parsed.get("events") or [])[:3]]
    assessment = {"direction": round(direction, 3), "confidence": round(confidence, 3), "events": events, "model": model, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    CACHE.set(cache_key, assessment)
    return assessment
