from __future__ import annotations

import re
from datetime import datetime

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
