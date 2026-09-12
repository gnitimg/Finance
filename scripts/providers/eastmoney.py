from __future__ import annotations

import re
import time

from ..cache import CACHE
from ..http_client import request_json
from ..models import FinanceError, utc_now

# East Money's public fund-flow feed. The push2his daykline endpoint keeps the
# latest ~120 trading days; the last row carries today's running value while
# the session is open. secid prefixes: 1/0 = SH/SZ, 116 = HK, 105/106/107 = US.
_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_US_MARKETS = ("105", "106", "107")
_US_RESOLVED: dict[str, str] = {}
_BREAKER_KEY = "flow:eastmoney:cooldown"
_KLINE_BREAKER_KEY = "sector:eastmoney:kline-cooldown"
_COOLDOWN_SECONDS = 900
INDUSTRY_TTL = 7 * 86_400
BOARD_DIRECTORY_TTL = 300
SECTOR_CONTEXT_TTL = 300
SECTOR_KLINE_TTL = 1800
SECTOR_STALE_TTL = 7 * 86_400
SECTOR_KLINE_LIMIT = 1200


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


def cached_daily_flow(market: str, symbol: str, max_age: int = 1800) -> list[dict] | None:
    """Read optional flow context without delaying an interactive analysis."""
    cached, _cache_meta = CACHE.get(f"flow:{market}:{symbol}", max_age)
    return cached


_DELAY_URL = "https://push2delay.eastmoney.com/api/qt"
_HIS_URL = "https://push2his.eastmoney.com/api/qt"
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "https://quote.eastmoney.com/"}


def _fetch_json(url: str) -> dict:
    # push2delay accepts urllib but push2his drops it mid-TLS; the shared
    # client's curl fallback covers both without leaking that detail here.
    payload, _meta = request_json(url, headers=_BROWSER_HEADERS, timeout=10, attempts=1)
    return payload if isinstance(payload, dict) else {}


def stock_industry(market: str, symbol: str) -> str | None:
    identifier = secid(market, symbol)
    if not identifier:
        return None
    cache_key = f"industry:{market}:{symbol}"
    cached, _meta = CACHE.get(cache_key, INDUSTRY_TTL)
    if isinstance(cached, dict) and "industry" in cached:
        return cached.get("industry")
    url = f"{_DELAY_URL}/stock/get?secid={identifier}&fields=f127"
    try:
        payload = _fetch_json(url)
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        return stale.get("industry") if isinstance(stale, dict) else None
    industry = (payload.get("data") or {}).get("f127") or None
    if industry:
        CACHE.set(cache_key, {"industry": industry})
    return industry


def board_directory() -> list[dict]:
    """Industry boards sorted by change_pct desc, with main-force inflow."""
    cache_key = "sector:board-directory"
    cached, _meta = CACHE.get(cache_key, BOARD_DIRECTORY_TTL)
    if isinstance(cached, list):
        return cached
    try:
        payload = _fetch_json(f"{_DELAY_URL}/clist/get?pn=1&pz=500&po=1&np=1&fltt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f62")
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, 86_400)
        if isinstance(stale, list):
            return stale
        raise
    rows = ((payload.get("data") or {}).get("diff")) or []
    directory = [{"code": r.get("f12"), "name": r.get("f14"), "change_pct": r.get("f3"), "main_net": r.get("f62")} for r in rows if r.get("f12")]
    if directory:
        CACHE.set(cache_key, directory)
    return directory


def kline(identifier: str, limit: int = 120) -> list[dict]:
    """Daily closes for an index or board identifier (e.g. 1.000300, 90.BK0428)."""
    limit = max(2, min(int(limit), 1500))
    cache_key = f"kline:{identifier}:{limit}"
    cached, _meta = CACHE.get(cache_key, SECTOR_KLINE_TTL)
    if cached is not None and isinstance(cached, list):
        return cached
    cooldown, _cooldown_meta = CACHE.get(_KLINE_BREAKER_KEY, SECTOR_KLINE_TTL)
    if float((cooldown or {}).get("until") or 0) > time.time():
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, list):
            return stale
        raise FinanceError("PROVIDER_COOLDOWN", "East Money board history skipped during cooldown", "eastmoney")
    url = f"{_HIS_URL}/stock/kline/get?secid={identifier}&klt=101&fqt=1&lmt={limit}&end=20500101&fields1=f1,f2,f3&fields2=f51,f53"
    try:
        payload = _fetch_json(url)
    except Exception:
        CACHE.set(_KLINE_BREAKER_KEY, {"until": time.time() + SECTOR_KLINE_TTL})
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, list):
            return stale
        raise
    rows = ((payload.get("data") or {}).get("klines")) or []
    out = []
    for line in rows:
        parts = str(line).split(",")
        try:
            out.append({"date": parts[0], "close": float(parts[1])})
        except (ValueError, IndexError):
            continue
    if out:
        out.sort(key=lambda row: row["date"])
        CACHE.set(cache_key, out)
        return out
    stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
    return stale if isinstance(stale, list) else []


def cached_kline(identifier: str, limit: int = SECTOR_KLINE_LIMIT, max_age: int = SECTOR_KLINE_TTL, stale_age: int = SECTOR_STALE_TTL) -> list[dict] | None:
    """Read board/index history without doing network I/O."""
    limit = max(2, min(int(limit), 1500))
    cached, _meta = CACHE.get(f"kline:{identifier}:{limit}", max_age, stale_age)
    return cached if isinstance(cached, list) and cached else None


def _enrich_series(rows: list[dict]) -> list[dict]:
    out = []
    for index, row in enumerate(rows):
        prev = rows[index - 1]["close"] if index else row["close"]
        window = rows[max(0, index - 19):index + 1]
        out.append({
            "date": row["date"],
            "close": row["close"],
            "return_1": row["close"] / prev - 1 if prev else 0.0,
            "ma20_gap": row["close"] / (sum(w["close"] for w in window) / len(window)) - 1 if window else 0.0,
            "momentum_20": row["close"] / rows[index - 20]["close"] - 1 if index >= 20 and rows[index - 20]["close"] else 0.0,
        })
    return out


def refresh_flow_today(market: str, symbol: str) -> list[dict] | None:
    """In-session flow refresh: one push2delay call returns today's running
    row only. Merged over the cached history so the interactive path never
    needs the rate-limited push2his host during trading hours."""
    identifier = secid(market, symbol)
    if not identifier:
        return None
    url = f"{_DELAY_URL}/stock/fflow/daykline/get?lmt=1&klt=101&secid={identifier}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
    try:
        rows = _parse_rows(_fetch_json(url))
    except (FinanceError, Exception):
        return None
    if not rows:
        return None
    history, _meta = CACHE.get(f"flow:{market}:{symbol}", 86_400) or (None, {})
    if isinstance(history, list) and history:
        merged = [row for row in history if row.get("date") != rows[0]["date"]] + rows
        merged.sort(key=lambda row: row["date"])
    else:
        merged = rows
    CACHE.set(f"flow:{market}:{symbol}", merged)
    return merged


def sector_identity(market: str, symbol: str) -> dict | None:
    """Resolve an A-share to its East Money industry board for seven days."""
    if market != "cn":
        return None
    cache_key = f"sectorid:{market}:{symbol}"
    cached, _meta = CACHE.get(cache_key, INDUSTRY_TTL)
    if isinstance(cached, dict):
        return cached
    industry = stock_industry(market, symbol)
    if not industry:
        identity = {"industry": None, "board_code": None, "board_name": None}
        CACHE.set(cache_key, identity)
        return identity
    try:
        directory = board_directory()
        board = next((row for row in directory if row.get("name") == industry), None)
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, dict):
            return stale
        raise
    identity = {"industry": industry, "board_code": (board or {}).get("code"), "board_name": (board or {}).get("name") or industry}
    # An empty directory during provider degradation must not freeze a missing
    # board mapping for seven days. Cache only a resolved BK identifier.
    if identity.get("board_code"):
        CACHE.set(cache_key, identity)
    return identity


def cached_sector_context(market: str, symbol: str, max_age: int = SECTOR_KLINE_TTL) -> dict | None:
    """Read optional A-share sector context without slowing an analysis."""
    if market != "cn":
        return None
    cached, meta = CACHE.get(f"sectorctx:{symbol}", max_age)
    return {**cached, "cache": meta} if isinstance(cached, dict) and cached.get("industry") else None


def cached_market_reference(board_code: str | None, max_age: int = SECTOR_KLINE_TTL) -> tuple[list[dict], list[dict]] | None:
    """Return pre-warmed causal board/index series without network I/O."""
    if not board_code:
        return None
    board_rows = cached_kline(f"90.{board_code}", SECTOR_KLINE_LIMIT, max_age)
    index_rows = cached_kline("1.000300", SECTOR_KLINE_LIMIT, max_age)
    if not board_rows and not index_rows:
        return None
    return _enrich_series(board_rows or []), _enrich_series(index_rows or [])


def sector_context(market: str, symbol: str) -> dict | None:
    """Industry board plus broad-market context for an A-share, fully cached.

    Returns None when the symbol has no EM industry classification (HK/US etc).
    """
    if market != "cn":
        return None
    cache_key = f"sectorctx:{symbol}"
    cached, meta = CACHE.get(cache_key, SECTOR_CONTEXT_TTL)
    if cached is not None and isinstance(cached, dict):
        return {**cached, "cache": meta}
    identity = sector_identity(market, symbol) or {}
    industry = identity.get("industry")
    if not industry:
        empty = {"industry": None, "board_code": None, "board_name": None, "source": "East Money", "as_of": utc_now()}
        CACHE.set(cache_key, empty)
        return {**empty, "cache": {"cached": False, "stale": False, "age_seconds": 0}}
    directory = board_directory()
    board = next((row for row in directory if row.get("code") == identity.get("board_code")), None)
    if board is None:
        board = next((row for row in directory if row.get("name") == industry), None)
    context = {**identity, "source": "East Money", "as_of": utc_now()}
    if board:
        # clist heat is on push2delay (rarely blocked); klines are on push2his
        # (rate-limited). A blocked kline host must not discard the heat data.
        context.update({
            "board_change_pct_now": board.get("change_pct"),
            "board_main_net": board.get("main_net"),
        })
        try:
            rows = _enrich_series(kline(f"90.{board['code']}", SECTOR_KLINE_LIMIT))
            latest = rows[-1] if rows else {}
            context.update({"board_return_1": latest.get("return_1"), "board_ma20_gap": latest.get("ma20_gap"), "board_momentum_20": latest.get("momentum_20")})
        except Exception:
            context["klines_blocked"] = True
    try:
        index_rows = _enrich_series(kline("1.000300", SECTOR_KLINE_LIMIT))
        index_latest = index_rows[-1] if index_rows else {}
        context.update({"index_return_1": index_latest.get("return_1"), "index_ma20_gap": index_latest.get("ma20_gap"), "index_momentum_20": index_latest.get("momentum_20")})
    except Exception:
        context["klines_blocked"] = True
    context["top_boards"] = directory[:6]
    CACHE.set(cache_key, context)
    return {**context, "cache": {"cached": False, "stale": False, "age_seconds": 0}}
