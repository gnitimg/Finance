from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

from ..cache import CACHE
from ..http_client import request_bytes, request_json
from ..symbols import normalize, yahoo_symbol
from .sentiment import analyze as sentiment_analysis


def _gdelt(query_text: str, limit: int) -> list[dict]:
    params = urlencode({"query": query_text, "mode": "artlist", "maxrecords": min(limit, 25), "format": "json", "sort": "hybridrel", "timespan": "7d"})
    payload, _ = request_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}", timeout=9, attempts=1)
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
    cache_key = f"news:{market}:{symbol}:{related_name}:{limit}"
    cached, cache_meta = CACHE.get(cache_key, 300)
    if cached:
        cached["cache"] = cache_meta
        return cached
    query_text = f'"{related_name}" OR {symbol}' if related_name and related_name.upper() != symbol else symbol
    providers = {"gdelt": lambda: _gdelt(query_text, limit), "yahoo": lambda: _yahoo(market, symbol, limit)}
    items, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
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
    result = {
        "asset": {"market": market, "symbol": symbol}, "items": unique,
        "sentiment": sentiment_analysis(unique), "provider_failures": failures,
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
