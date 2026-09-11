from __future__ import annotations

from ..cache import CACHE
from ..models import FinanceError
from ..symbols import METAL_SINA, normalize
from .coingecko import market_chart, quote_crypto
from .sina import quote_cn, quote_hf
from .yahoo import chart


TTL = {"cn": 2, "hk": 3, "us": 3, "crypto": 5, "etf": 3, "fund": 15, "future": 3, "metal": 3}
STALE = {"cn": 900, "hk": 1800, "us": 1800, "crypto": 300, "etf": 1800, "fund": 3600, "future": 900, "metal": 900}


def _with_cache_meta(result: dict, meta: dict) -> dict:
    result = dict(result)
    result["cache"] = meta
    return result


def quote(market: str, symbol: str) -> dict:
    market, symbol = normalize(market, symbol)
    key = f"quote:{market}:{symbol}"
    cached, meta = CACHE.get(key, TTL[market])
    if cached:
        return _with_cache_meta(cached, meta)
    try:
        if market == "cn":
            result, http_meta = quote_cn(symbol)
        elif market == "crypto":
            result, http_meta = quote_crypto(symbol)
        elif market == "metal" and symbol in METAL_SINA:
            try:
                result, http_meta = quote_hf(METAL_SINA[symbol], symbol)
            except FinanceError:
                result, http_meta = chart(market, symbol, "5d", "15m")
                result["warnings"] = list(result.get("warnings") or []) + ["Sina snapshot unavailable; quote uses the delayed Yahoo futures feed."]
        else:
            result, http_meta = chart(market, symbol, "5d", "15m")
        result["provider_timing"] = http_meta
        CACHE.set(key, result)
        return _with_cache_meta(result, {"cached": False, "stale": False, "age_seconds": 0})
    except FinanceError as exc:
        stale, stale_meta = CACHE.get(key, 0, STALE[market])
        if stale:
            stale["warnings"] = list(stale.get("warnings") or []) + [f"Live provider failed; serving stale cache: {exc.message}"]
            return _with_cache_meta(stale, stale_meta)
        raise


def history(market: str, symbol: str, range_name: str = "3mo", interval: str = "1d") -> dict:
    market, symbol = normalize(market, symbol)
    key = f"history:{market}:{symbol}:{range_name}:{interval}"
    ttl = 5 if interval != "1d" else 300
    cached, meta = CACHE.get(key, ttl)
    if cached:
        return _with_cache_meta(cached, meta)
    try:
        if market == "crypto":
            days = {"1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}.get(range_name, 90)
            bars, http_meta = market_chart(symbol, days)
            current = quote(market, symbol)
            result = {"asset": current["asset"], "quote": current["quote"], "history": bars, "provider_timing": http_meta}
        else:
            result, http_meta = chart(market, symbol, range_name, interval)
            result["provider_timing"] = http_meta
            if market == "cn":
                try:
                    live = quote_cn(symbol)[0]
                    result["quote"] = live["quote"]
                    result["asset"].update({k: v for k, v in live["asset"].items() if v})
                except FinanceError:
                    result.setdefault("warnings", []).append("Sina snapshot unavailable; quote uses Yahoo chart metadata")
            elif market == "metal" and symbol in METAL_SINA:
                try:
                    live = quote_hf(METAL_SINA[symbol], symbol)[0]
                    stale_fields = {key: value for key, value in result["quote"].items() if live["quote"].get(key) is None}
                    result["quote"] = live["quote"]
                    result["quote"].update(stale_fields)
                    result.setdefault("warnings", []).append("Bars follow the delayed Yahoo futures feed; the headline quote is the near-live Sina snapshot.")
                except FinanceError:
                    result.setdefault("warnings", []).append("Sina snapshot unavailable; quote uses the delayed Yahoo futures feed.")
        CACHE.set(key, result)
        return _with_cache_meta(result, {"cached": False, "stale": False, "age_seconds": 0})
    except FinanceError as exc:
        stale, stale_meta = CACHE.get(key, 0, STALE[market])
        if stale:
            stale["warnings"] = list(stale.get("warnings") or []) + [f"History provider failed; serving stale cache: {exc.message}"]
            return _with_cache_meta(stale, stale_meta)
        raise
