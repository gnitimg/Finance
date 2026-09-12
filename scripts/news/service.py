from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

import re

from ..cache import CACHE
from ..http_client import request_bytes, request_json
from ..models import FinanceError
from ..providers.eastmoney import secid as eastmoney_secid
from ..symbols import normalize, yahoo_symbol

_TAG_RE = re.compile(r"<[^>]+>")


def _eastmoney(market: str, symbol: str, limit: int) -> list[dict]:
    """East Money per-stock news: the only keyless source with real A-share,
    HK, and US headline coverage in Chinese."""
    identifier = eastmoney_secid(market, symbol)
    if not identifier:
        return []
    payload, _meta = request_json(
        f"https://np-listapi.eastmoney.com/comm/web/getListInfo?client=web&mTypeAndCode={identifier}&pageSize={min(limit, 20)}&type=0",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "https://quote.eastmoney.com/"},
        timeout=8,
        attempts=1,
    )
    rows = ((payload.get("data") or {}).get("list")) if isinstance(payload, dict) else None
    result = []
    for row in rows or []:
        title = _TAG_RE.sub("", str(row.get("Art_Title") or "")).strip()
        url = str(row.get("Art_Url") or row.get("Art_OriginUrl") or "").strip()
        if not title or not url:
            continue
        result.append({
            "title": title,
            "url": url,
            "source": "东方财富",
            "published_at": row.get("Art_ShowTime"),
            "language": "中文",
            "summary": None,
        })
    return result
from . import siliconflow
from .sentiment import analyze as sentiment_analysis

GDELT_FAILURE_THRESHOLD = 2
GDELT_COOLDOWN_SECONDS = 600
_GDELT_STATE = {"consecutive_failures": 0}
_GDELT_COOLDOWN_KEY = "news:gdelt:cooldown"


def _news_cache_key(market: str, symbol: str) -> str:
    return f"news:{market}:{symbol}"


def cached_news(market: str, symbol: str, max_age: int = 900) -> dict | None:
    """Return exogenous context without starting network work on a quote path."""
    market, symbol = normalize(market, symbol)
    cached, cache_meta = CACHE.get(_news_cache_key(market, symbol), max_age)
    if not cached:
        return None
    cached["cache"] = cache_meta
    return cached


def _gdelt_cooldown_until() -> float:
    marker, _ = CACHE.get(_GDELT_COOLDOWN_KEY, 3600)
    return float((marker or {}).get("until") or 0)


def _gdelt(query_text: str, limit: int) -> list[dict]:
    now = time.time()
    if now < _gdelt_cooldown_until():
        raise FinanceError("PROVIDER_COOLDOWN", "GDELT skipped during failure cooldown", "gdelt")
    try:
        params = urlencode({"query": query_text, "mode": "artlist", "maxrecords": min(limit, 25), "format": "json", "sort": "hybridrel", "timespan": "7d"})
        payload, _ = request_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}", timeout=4.5, attempts=1)
    except Exception:
        failures = _GDELT_STATE["consecutive_failures"] + 1
        _GDELT_STATE["consecutive_failures"] = failures
        if failures >= GDELT_FAILURE_THRESHOLD or _gdelt_cooldown_until() > now:
            CACHE.set(_GDELT_COOLDOWN_KEY, {"until": now + GDELT_COOLDOWN_SECONDS})
        raise
    _GDELT_STATE["consecutive_failures"] = 0
    if _gdelt_cooldown_until():
        CACHE.set(_GDELT_COOLDOWN_KEY, {"until": 0})
    articles = payload.get("articles") if isinstance(payload, dict) else []
    result = []
    for item in articles or []:
        result.append({"title": item.get("title"), "url": item.get("url"), "source": item.get("domain") or "GDELT", "published_at": item.get("seendate"), "language": item.get("language"), "summary": None})
    return result


def _yahoo(market: str, symbol: str, limit: int) -> list[dict]:
    remote = yahoo_symbol(market, symbol) if market != "crypto" else symbol
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(remote)}&region=US&lang=en-US"
    body, _ = request_bytes(url, timeout=8, attempts=1)
    root = ET.fromstring(body)
    result = []
    for item in root.findall(".//item")[:limit]:
        result.append({
            "title": item.findtext("title"), "url": item.findtext("link"), "source": "Yahoo Finance",
            "published_at": item.findtext("pubDate"), "language": "English", "summary": item.findtext("description"),
        })
    return result


def get_news(market: str, symbol: str, limit: int = 12, related_name: str | None = None) -> dict:
    market, symbol = normalize(market, symbol)
    limit = max(1, min(int(limit), 25))
    related_name = (related_name or "").strip()
    cache_key = _news_cache_key(market, symbol)
    cached, cache_meta = CACHE.get(cache_key, 300)
    if cached:
        cached["cache"] = cache_meta
        cached["items"] = list(cached.get("items") or [])[:limit]
        return cached
    query_text = f'"{related_name}" OR {symbol}' if related_name and related_name.upper() != symbol else symbol
    providers = {
        "gdelt": lambda: _gdelt(query_text, limit),
        "yahoo": lambda: _yahoo(market, symbol, limit),
        "eastmoney": lambda: _eastmoney(market, symbol, limit),
    }
    items, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(task): name for name, task in providers.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                items.extend(future.result())
            except Exception as exc:
                failures.append({"provider": name, "message": str(exc)[:180]})
    seen, unique = set(), []
    for item in items:
        key = (item.get("url") or item.get("title") or "").strip()
        if not key or key in seen or not item.get("title"):
            continue
        seen.add(key)
        unique.append(item)
    unique = unique[:limit]
    sentiment = sentiment_analysis(unique, entity=related_name or symbol)
    if unique:
        # Routine news scoring stays deterministic.  The optional SiliconFlow
        # endpoint is a relevance reranker, not a generative analysis call.
        scorer = siliconflow if siliconflow.enabled() else None
        if scorer is not None:
            try:
                scores = scorer.score_items(unique)
                sentiment = scorer.apply(sentiment, unique, scores)
            except FinanceError as exc:
                failures.append({"provider": scorer.__name__.split(".")[-1], "message": exc.message})
    result = {
        "asset": {"market": market, "symbol": symbol}, "items": unique,
        "sentiment": sentiment, "provider_failures": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "cache": {"cached": False, "stale": False, "age_seconds": 0},
        "warnings": ["Sentiment confidence measures source and keyword evidence quality; it is not a return probability."],
    }
    if unique:
        CACHE.set(cache_key, result)
        return result
    stale, stale_meta = CACHE.get(cache_key, 0, 21600)
    if stale:
        stale["cache"] = stale_meta
        stale["provider_failures"] = failures
        stale["warnings"] = list(stale.get("warnings") or []) + ["Live news sources failed; serving an explicitly stale cache."]
        return stale
    return result
