from __future__ import annotations

import re

from ..cache import CACHE
from ..http_client import request_json
from ..models import FinanceError

# East Money's public fund-flow feed. The push2his daykline endpoint keeps the
# latest ~120 trading days; the last row carries today's running value while
# the session is open. secid prefixes: 1/0 = SH/SZ, 116 = HK, 105/106/107 = US.
_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_US_MARKETS = ("105", "106", "107")
_US_RESOLVED: dict[str, str] = {}
_BREAKER_KEY = "flow:eastmoney:cooldown"
import time

_COOLDOWN_SECONDS = 900


def _cooldown_until() -> float:
    marker, _ = CACHE.get(_BREAKER_KEY, 7200)
    return float((marker or {}).get("until") or 0)


def secid(market: str, symbol: str) -> str | None:
    if market == "cn":
        return ("1." if symbol.startswith(("6", "9", "5")) else "0.") + symbol
    if market == "hk":
        return "116." + symbol
    if market == "us":
        resolved = _US_RESOLVED.get(symbol)
        return (resolved or "105.") + symbol
    return None


def _parse_rows(payload) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = (data or {}).get("klines") or []
    rows = []
    for line in klines:
        parts = str(line).split(",")
        try:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]),
                "super_net": float(parts[2]) if len(parts) > 2 else None,
                "big_net": float(parts[3]) if len(parts) > 3 else None,
                "medium_net": float(parts[4]) if len(parts) > 4 else None,
                "small_net": float(parts[5]) if len(parts) > 5 else None,
            })
        except (ValueError, IndexError):
            continue
    return rows


def daily_flow(market: str, symbol: str, limit: int = 120) -> tuple[list[dict], dict]:
    base = secid(market, symbol)
    if not base:
        raise FinanceError("UNSUPPORTED_MARKET", f"No fund-flow coverage for {market}", "eastmoney")
    cache_key = f"flow:{market}:{symbol}"
    cached, cache_meta = CACHE.get(cache_key, 600)
    if cached:
        return cached, {"transport": "cache", "elapsed_ms": None}
    if time.time() < _cooldown_until():
        raise FinanceError("PROVIDER_COOLDOWN", "East Money skipped during failure cooldown", "eastmoney")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "https://quote.eastmoney.com/"}
    # US listings sit on 105/106/107 depending on exchange; remember the code
    # that returns data so later requests go straight to it.
    candidates = [base]
    if market == "us" and base.startswith("105."):
        candidates = [code + "." + symbol for code in _US_MARKETS]
    for identifier in candidates:
        url = f"{_HISTORY_URL}?lmt={max(2, min(int(limit), 120))}&klt=101&secid={identifier}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
        payload, http_meta = request_json(url, timeout=8, attempts=2, headers=headers)
        rows = _parse_rows(payload)
        if rows:
            if market == "us":
                _US_RESOLVED[symbol] = identifier.split(".", 1)[0]
            CACHE.set(cache_key, rows)
            return rows, http_meta
    failures, _ = CACHE.get("flow:eastmoney:failures", 86400) or ({"count": 0}, {})
    count = int((failures or {}).get("count") or 0) + 1
    CACHE.set("flow:eastmoney:failures", {"count": count})
    if count >= 2:
        CACHE.set(_BREAKER_KEY, {"until": time.time() + _COOLDOWN_SECONDS})
        CACHE.set("flow:eastmoney:failures", {"count": 0})
    raise FinanceError("NO_DATA", f"East Money returned no fund flow for {market}:{symbol}", "eastmoney")
