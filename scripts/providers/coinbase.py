from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import FinanceError
from ..http_client import request_json

BASE_URL = "https://api.exchange.coinbase.com"
GRANULARITY = {"5m": 300, "15m": 900, "30m": 1800, "1d": 86_400}
MAX_CANDLES_PER_CALL = 300


def _product(symbol: str) -> str:
    return f"{symbol.upper()}-USD"


def _iso(epoch: int | float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def quote_realtime(symbol: str) -> tuple[dict, dict]:
    product = _product(symbol)
    ticker_payload, ticker_meta = request_json(f"{BASE_URL}/products/{product}/ticker", timeout=6, attempts=1)
    stats_payload, _stats_meta = request_json(f"{BASE_URL}/products/{product}/stats", timeout=6, attempts=1)
    try:
        price = float(ticker_payload["price"])
        opened = float(stats_payload["open"])
        high = float(stats_payload["high"])
        low = float(stats_payload["low"])
        volume = float(stats_payload["volume"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FinanceError("INVALID_SCHEMA", f"Coinbase returned invalid market data for {product}", "coinbase") from exc
    change = price - opened
    stamp = ticker_payload.get("time")
    as_of = None
    if stamp:
        try:
            as_of = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            as_of = None
    return {
        "asset": {"market": "crypto", "symbol": symbol.upper(), "provider_symbol": product, "name": symbol.upper(), "type": "stablecoin" if symbol.upper() in {"USDT", "USDC", "DAI"} else "crypto", "currency": "USD", "exchange": "Coinbase", "timezone": "UTC"},
        "quote": {
            "price": price,
            "previous_close": opened,
            "change": change,
            "change_pct": change / opened * 100 if opened else None,
            "open": opened,
            "high": high,
            "low": low,
            "volume": volume,
            "market_state": "OPEN_24_7",
            "as_of": as_of,
            "source": "Coinbase",
            "feed": "Coinbase Exchange public feed",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, ticker_meta


def _candle_batch(product: str, granularity: int, start: datetime, end: datetime) -> tuple[list[dict], dict]:
    payload, meta = request_json(
        f"{BASE_URL}/products/{product}/candles?granularity={granularity}&start={start.isoformat().replace('+00:00', 'Z')}&end={end.isoformat().replace('+00:00', 'Z')}",
        timeout=10,
        attempts=2,
    )
    if not isinstance(payload, list):
        raise FinanceError("INVALID_SCHEMA", f"Coinbase candles invalid for {product}", "coinbase")
    bars = []
    for row in payload:
        try:
            stamp, low, high, opened, close, volume = (float(value) for value in row[:6])
        except (ValueError, TypeError):
            continue
        bars.append({"time": _iso(stamp), "timestamp": int(stamp), "open": opened, "high": high, "low": low, "close": close, "adjusted_close": close, "volume": volume})
    return bars, meta


def candles(symbol: str, interval: str, days: int) -> tuple[list[dict], dict]:
    """Real-time OHLCV from Coinbase. Coinbase returns newest-first, so older
    windows are fetched backwards in capped batches and merged ascending."""
    product = _product(symbol)
    granularity = GRANULARITY.get(interval)
    if not granularity:
        raise FinanceError("UNSUPPORTED_INTERVAL", f"Coinbase candles unsupported for {interval}", "coinbase")
    span = max(1, days) * 86_400
    bucket = granularity
    end = datetime.now(timezone.utc)
    bars: list[dict] = []
    meta = {"status": 200, "transport": "coinbase"}
    calls = 0
    cursor = end
    while calls < 9:
        window = min(span - len(bars) * bucket, MAX_CANDLES_PER_CALL * bucket)
        start = cursor - timedelta(seconds=max(window, bucket))
        batch, batch_meta = _candle_batch(product, granularity, start, cursor)
        meta = batch_meta
        calls += 1
        bars = batch + bars
        if len(batch) < MAX_CANDLES_PER_CALL or start.timestamp() <= end.timestamp() - span or not batch:
            break
        cursor = start
    if not bars:
        raise FinanceError("NO_DATA", f"Coinbase returned no candles for {product}", "coinbase")
    bars.sort(key=lambda bar: bar["timestamp"])
    return bars, meta
