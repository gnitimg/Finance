from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http_client import request_bytes
from ..models import FinanceError


def quote_cn(symbol: str) -> tuple[dict, dict]:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    body, http_meta = request_bytes(
        f"https://hq.sinajs.cn/list={prefix}{symbol}",
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=8,
        attempts=2,
    )
    text = body.decode("gb18030", errors="replace")
    match = re.search(r'="(.*)"', text)
    if not match or not match.group(1):
        raise FinanceError("NO_DATA", f"Sina returned no quote for {symbol}", "sina")
    parts = match.group(1).split(",")
    if len(parts) < 32:
        raise FinanceError("INVALID_SCHEMA", "Sina response is incomplete", "sina")
    try:
        open_price = float(parts[1])
        previous = float(parts[2])
        price = float(parts[3])
        high = float(parts[4])
        low = float(parts[5])
        volume = float(parts[8])
        turnover = float(parts[9])
    except ValueError as exc:
        raise FinanceError("INVALID_SCHEMA", "Sina response contains invalid numbers", "sina") from exc
    change = price - previous if previous else None
    as_of = None
    market_state = "CLOSED"
    try:
        quote_time = datetime.strptime(f"{parts[30]} {parts[31]} +0800", "%Y-%m-%d %H:%M:%S %z")
        as_of = quote_time.isoformat(timespec="seconds")
        minute = quote_time.hour * 60 + quote_time.minute
        market_state = "REGULAR" if 570 <= minute <= 690 or 780 <= minute <= 900 else "CLOSED"
    except (ValueError, IndexError):
        pass
    return {
        "asset": {"market": "cn", "symbol": symbol, "provider_symbol": prefix + symbol, "name": parts[0], "currency": "CNY", "exchange": "SSE" if prefix == "sh" else "SZSE", "timezone": "Asia/Shanghai"},
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change / previous * 100 if change is not None and previous else None,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "turnover": turnover,
            "market_state": market_state,
            "as_of": as_of,
            "source": "Sina Finance",
            "feed": "Public A-share snapshot",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, http_meta


def quote_hf(sina_code: str, symbol: str) -> tuple[dict, dict]:
    # Sina international-futures snapshot: near-live, timestamps in Beijing
    # time. Field order (verified against the live feed):
    # 0 last, 2 bid, 3 ask, 4 high, 5 low, 6 time, 7 prev settlement,
    # 8 open, 12 date, 13 name. No cumulative day volume is provided.
    body, http_meta = request_bytes(
        f"https://hq.sinajs.cn/list={sina_code}",
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=8,
        attempts=2,
    )
    text = body.decode("gb18030", errors="replace")
    match = re.search(r'="(.*)"', text)
    if not match or not match.group(1):
        raise FinanceError("NO_DATA", f"Sina returned no quote for {sina_code}", "sina")
    parts = match.group(1).split(",")
    if len(parts) < 14:
        raise FinanceError("INVALID_SCHEMA", "Sina response is incomplete", "sina")
    try:
        price = float(parts[0])
        previous = float(parts[7])
        open_price = float(parts[8])
        high = float(parts[4])
        low = float(parts[5])
    except ValueError as exc:
        raise FinanceError("INVALID_SCHEMA", "Sina response contains invalid numbers", "sina") from exc
    change = price - previous if previous else None
    as_of = None
    market_state = "CLOSED"
    try:
        quote_time = datetime.strptime(f"{parts[12]} {parts[6]} +0800", "%Y-%m-%d %H:%M:%S %z")
        as_of = quote_time.isoformat(timespec="seconds")
        # Futures trade nearly around the clock; a snapshot older than three
        # minutes means the session is closed rather than the feed stalled.
        market_state = "REGULAR" if abs(datetime.now(timezone.utc).timestamp() - quote_time.timestamp()) <= 180 else "CLOSED"
    except (ValueError, IndexError):
        pass
    return {
        "asset": {"market": "metal", "symbol": symbol, "provider_symbol": sina_code, "name": parts[13] or sina_code, "currency": "USD", "exchange": "COMEX/NYMEX", "timezone": "Asia/Shanghai"},
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change / previous * 100 if change is not None and previous else None,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": None,
            "market_state": market_state,
            "as_of": as_of,
            "source": "Sina Finance",
            "feed": "Public international futures snapshot",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, http_meta


def quote_gb(symbol: str) -> tuple[dict, dict]:
    """Sina US snapshot (gb_ prefix). Near-live during the session; the last
    trade time arrives as an EDT/EST string, so liveness is judged by the age
    of the Beijing timestamp instead of parsing US zone names."""
    body, http_meta = request_bytes(
        f"https://hq.sinajs.cn/list=gb_{symbol.lower()}",
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=8,
        attempts=2,
    )
    text = body.decode("gb18030", errors="replace")
    match = re.search(r'="(.*)"', text)
    if not match or not match.group(1):
        raise FinanceError("NO_DATA", f"Sina returned no quote for gb_{symbol}", "sina")
    parts = match.group(1).split(",")
    if len(parts) < 27:
        raise FinanceError("INVALID_SCHEMA", "Sina response is incomplete", "sina")
    try:
        price = float(parts[1])
        change = float(parts[4])
        open_price = float(parts[5])
        high = float(parts[6])
        low = float(parts[7])
        volume = float(parts[10])
        previous = float(parts[26])
    except ValueError as exc:
        raise FinanceError("INVALID_SCHEMA", "Sina response contains invalid numbers", "sina") from exc
    as_of = None
    market_state = "CLOSED"
    try:
        quote_time = datetime.strptime(parts[3], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
        as_of = quote_time.isoformat(timespec="seconds")
        market_state = "REGULAR" if abs(datetime.now(timezone.utc).timestamp() - quote_time.timestamp()) <= 180 else "CLOSED"
    except ValueError:
        pass
    return {
        "asset": {"market": "us", "symbol": symbol, "provider_symbol": f"gb_{symbol.lower()}", "name": parts[0] or symbol, "currency": "USD", "exchange": None, "timezone": "America/New_York"},
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change / previous * 100 if previous else None,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "market_state": market_state,
            "as_of": as_of,
            "source": "Sina Finance",
            "feed": "Public US snapshot",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, http_meta


def quote_hk(symbol: str) -> tuple[dict, dict]:
    """Sina HK snapshot (hk prefix): near-live with day OHLC and turnover."""
    body, http_meta = request_bytes(
        f"https://hq.sinajs.cn/list=hk{symbol}",
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=8,
        attempts=2,
    )
    text = body.decode("gb18030", errors="replace")
    match = re.search(r'="(.*)"', text)
    if not match or not match.group(1):
        raise FinanceError("NO_DATA", f"Sina returned no quote for hk{symbol}", "sina")
    parts = match.group(1).split(",")
    if len(parts) < 19:
        raise FinanceError("INVALID_SCHEMA", "Sina response is incomplete", "sina")
    try:
        open_price = float(parts[2])
        previous = float(parts[3])
        high = float(parts[4])
        low = float(parts[5])
        price = float(parts[6])
        change = float(parts[7])
        turnover = float(parts[11])
        volume = float(parts[12])
    except ValueError as exc:
        raise FinanceError("INVALID_SCHEMA", "Sina response contains invalid numbers", "sina") from exc
    as_of = None
    market_state = "CLOSED"
    try:
        quote_time = datetime.strptime(f"{parts[17]} {parts[18]} +0800", "%Y/%m/%d %H:%M %z")
        as_of = quote_time.isoformat(timespec="seconds")
        market_state = "REGULAR" if abs(datetime.now(timezone.utc).timestamp() - quote_time.timestamp()) <= 180 else "CLOSED"
    except ValueError:
        pass
    return {
        "asset": {"market": "hk", "symbol": symbol, "provider_symbol": f"hk{symbol}", "name": parts[1] or symbol, "currency": "HKD", "exchange": "HKEX", "timezone": "Asia/Hong_Kong"},
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change / previous * 100 if previous else None,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "turnover": turnover,
            "market_state": market_state,
            "as_of": as_of,
            "source": "Sina Finance",
            "feed": "Public HK snapshot",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, http_meta
