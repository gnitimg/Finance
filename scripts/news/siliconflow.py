from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..cache import CACHE
from ..config import env_bool
from ..models import FinanceError

RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
# Contrastive queries: a document's sentiment is the relevance gap between the
# good-news and bad-news queries, so plain keyword matching is not required.
POSITIVE_QUERY = "公司业绩利好，盈利增长，订单充足，股价上涨"
NEGATIVE_QUERY = "公司业绩恶化，亏损加剧，需求疲软，股价下跌"
BATCH_SIZE = 20
SCORE_TTL = 21_600
FAILURE_THRESHOLD = 2
COOLDOWN_SECONDS = 600
_BREAKER_KEY = "news:siliconflow:cooldown"
_FAILURES_KEY = "news:siliconflow:failures"


def enabled() -> bool:
    return env_bool("FINANCE_SENTIMENT_SILICONFLOW_ENABLED", False) and bool(os.getenv("SILICONFLOW_API_KEY", "").strip())


def _cooldown_until() -> float:
    marker, _ = CACHE.get(_BREAKER_KEY, 7200)
    return float((marker or {}).get("until") or 0)


def _cache_key(item: dict) -> str:
    digest = hashlib.sha1(str(item.get("url") or item.get("title") or "").encode("utf-8")).hexdigest()[:20]
    return f"sfsent:{digest}"


def _rerank_post(key: str, query: str, documents: list[str]) -> list[float]:
    body = json.dumps({"model": RERANK_MODEL, "query": query, "documents": documents, "top_n": len(documents), "return_documents": False}).encode("utf-8")
    request = urllib.request.Request(RERANK_URL, data=body, method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "gnitimg-finance/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    scores = [0.0] * len(documents)
    for entry in payload.get("results") or []:
        try:
            scores[int(entry["index"])] = max(0.0, min(1.0, float(entry["relevance_score"])))
        except (KeyError, ValueError, TypeError):
            continue
    return scores


def score_items(items: list[dict]) -> dict[str, float]:
    """Return {url: sentiment in [-1, 1]} via contrastive BAAI reranking."""
    if not enabled() or not items:
        return {}
    if time.time() < _cooldown_until():
        raise FinanceError("PROVIDER_COOLDOWN", "SiliconFlow skipped during failure cooldown", "siliconflow")
    key = os.getenv("SILICONFLOW_API_KEY", "").strip()
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
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        documents = [f"{item.get('title', '')}"[:400] for item in batch]
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                positive_future = pool.submit(_rerank_post, key, POSITIVE_QUERY, documents)
                negative_future = pool.submit(_rerank_post, key, NEGATIVE_QUERY, documents)
                positive_scores = positive_future.result()
                negative_scores = negative_future.result()
        except Exception as exc:
            failures, _ = CACHE.get(_FAILURES_KEY, 86_400) or ({"count": 0}, {})
            count = int((failures or {}).get("count") or 0) + 1
            CACHE.set(_FAILURES_KEY, {"count": count})
            if count >= FAILURE_THRESHOLD:
                CACHE.set(_BREAKER_KEY, {"until": time.time() + COOLDOWN_SECONDS})
                CACHE.set(_FAILURES_KEY, {"count": 0})
            raise FinanceError("SPECIALIST_UNAVAILABLE", f"SiliconFlow rerank failed: {type(exc).__name__}", "siliconflow") from exc
        for item, pos, neg in zip(batch, positive_scores, negative_scores):
            score = max(-1.0, min(1.0, pos - neg))
            CACHE.set(_cache_key(item), {"score": round(score, 4)})
            scores[str(item.get("url"))] = float(score)
    if scores:
        CACHE.set(_FAILURES_KEY, {"count": 0})
        CACHE.set(_BREAKER_KEY, {"until": 0})
    return scores


def apply(sentiment: dict, items: list[dict], rerank_scores: dict[str, float]) -> dict:
    """Blend reranker per-article scores with the lexicon aggregate (0.6 / 0.4)."""
    from . import llm_sentiment

    return llm_sentiment.apply(sentiment, items, rerank_scores)
