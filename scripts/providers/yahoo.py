from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from ..http_client import request_json
from ..models import FinanceError
from ..symbols import yahoo_symbol


RANGE_INTERVALS = {
    ("1d", "5m"), ("5d", "5m"), ("5d", "15m"), ("5d", "30m"), ("1mo", "1d"),
    ("3mo", "1d"), ("6mo", "1d"), ("1y", "1d"), ("2y", "1d"),
}


def _iso(timestamp: int | float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_closed_placeholder(previous: dict | None, bar: dict) -> bool:
    if not previous or float(bar.get("volume") or 0) != 0:
        return False
    close = bar.get("close")
    ohlc = [bar.get(name) for name in ("open", "high", "low", "close")]
    return close is not None and all(value is not None and float(value) == float(close) for value in ohlc) and float(previous["close"]) == float(close)


def chart(market: str, symbol: str, range_name: str = "3mo", interval: str = "1d") -> tuple[dict, dict]:
    if (range_name, interval) not in RANGE_INTERVALS:
        raise ValueError("unsupported range/interval combination")
    remote_symbol = yahoo_symbol(market, symbol)
    params = urlencode({"range": range_name, "interval": interval, "includePrePost": "false", "events": "div,splits"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(remote_symbol, safe='^.-')}?{params}"
    payload, http_meta = request_json(url, timeout=10, attempts=2)
    try:
        chart_root = payload["chart"]
        if chart_root.get("error"):
            raise FinanceError("PROVIDER_ERROR", str(chart_root["error"]), "yahoo")
        result = chart_root["result"][0]
        meta = result["meta"]
    except (KeyError, IndexError, TypeError) as exc:
        raise FinanceError("INVALID_SCHEMA", "Yahoo response is missing chart data", "yahoo") from exc

    timestamps = result.get("timestamp") or []
    quote_values = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj_values = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    bars = []
    for index, timestamp in enumerate(timestamps):
        def at(name):
            values = quote_values.get(name) or []
            return values[index] if index < len(values) else None
        close = at("close")
        if close is None:
            continue
        bar = {
            "time": _iso(timestamp),
            "timestamp": timestamp,
            "open": at("open"),
            "high": at("high"),
            "low": at("low"),
            "close": close,
            "adjusted_close": adj_values[index] if index < len(adj_values) else close,
            "volume": at("volume") or 0,
        }
        if not is_closed_placeholder(bars[-1] if bars else None, bar):
            bars.append(bar)

    price = meta.get("regularMarketPrice")
    # During an open session Yahoo can publish the live quote before today's
    # daily bar exists. Select the adjacent completed bar by date in that case.
    quote_timestamp = meta.get("regularMarketTime")
    last_bar_timestamp = bars[-1]["timestamp"] if bars else None
    quote_day = datetime.fromtimestamp(quote_timestamp, tz=timezone.utc).date() if quote_timestamp else None
    last_bar_day = datetime.fromtimestamp(last_bar_timestamp, tz=timezone.utc).date() if last_bar_timestamp else None
    if interval == "1d" and bars:
        if quote_day and last_bar_day and quote_day > last_bar_day:
            previous = bars[-1]["close"]
        elif len(bars) > 1:
            previous = bars[-2]["close"]
        else:
            previous = meta.get("previousClose") or meta.get("chartPreviousClose")
    else:
        previous = meta.get("previousClose") or meta.get("chartPreviousClose")
    if price is None and bars:
        price = bars[-1]["close"]
    if previous is None and len(bars) > 1:
        previous = bars[-2]["close"]
    if price is None:
        raise FinanceError("NO_DATA", f"No price data for {remote_symbol}", "yahoo")
    change = price - previous if previous not in (None, 0) else None
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    now = datetime.now(timezone.utc).timestamp()
    state = "REGULAR" if period.get("start", 0) <= now <= period.get("end", 0) else "CLOSED"
    provider_type = str(meta.get("instrumentType") or "").upper()
    detected_type = {
        "EQUITY": "equity", "ETF": "etf", "MUTUALFUND": "fund",
        "FUTURE": "future", "INDEX": "index", "CRYPTOCURRENCY": "crypto",
    }.get(provider_type, "equity")
    asset_type = market if market in {"etf", "fund", "future", "metal"} else detected_type
    quote_data = {
        "asset": {
            "market": market,
            "symbol": symbol,
            "provider_symbol": remote_symbol,
            "name": meta.get("longName") or meta.get("shortName") or remote_symbol,
            "currency": meta.get("currency") or ("HKD" if market == "hk" else "CNY" if market == "cn" else "USD"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "timezone": meta.get("exchangeTimezoneName"),
            "type": asset_type,
            "provider_type": provider_type or None,
        },
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change / previous * 100 if change is not None and previous else None,
            "open": meta.get("regularMarketOpen") or (bars[-1]["open"] if bars else None),
            "high": meta.get("regularMarketDayHigh") or (bars[-1]["high"] if bars else None),
            "low": meta.get("regularMarketDayLow") or (bars[-1]["low"] if bars else None),
            "volume": meta.get("regularMarketVolume") or (bars[-1]["volume"] if bars else None),
            "market_state": state,
            "as_of": _iso(meta.get("regularMarketTime") or (bars[-1]["timestamp"] if bars else None)),
            "source": "Yahoo Finance",
            "feed": "Public chart feed; exchange delay may apply",
            "realtime": False,
            "delayed_seconds": None,
        },
        "history": bars,
    }
    return quote_data, http_meta
