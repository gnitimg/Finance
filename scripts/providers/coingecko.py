from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from ..http_client import request_json
from ..models import FinanceError
from ..config import read_json
from ..symbols import STABLECOINS

CRYPTO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether", "USDC": "usd-coin", "DAI": "dai"}
RESOLVED_IDS: dict[str, str] = {}


def resolve_coin_id(symbol: str) -> str:
    symbol = symbol.upper()
    config = read_json("stablecoins.json").get(symbol, {})
    known = config.get("coingecko_id") or CRYPTO_IDS.get(symbol) or RESOLVED_IDS.get(symbol)
    if known:
        return known
    payload, _ = request_json(f"https://api.coingecko.com/api/v3/search?query={quote(symbol)}", timeout=8, attempts=2)
    candidates = [item for item in (payload.get("coins") if isinstance(payload, dict) else []) or [] if str(item.get("symbol") or "").upper() == symbol]
    candidates.sort(key=lambda item: item.get("market_cap_rank") if isinstance(item.get("market_cap_rank"), int) else 10**9)
    if not candidates or not candidates[0].get("id"):
        raise FinanceError("UNSUPPORTED_SYMBOL", f"No verified CoinGecko match for {symbol}", "coingecko")
    RESOLVED_IDS[symbol] = candidates[0]["id"]
    return RESOLVED_IDS[symbol]


def quote_crypto(symbol: str) -> tuple[dict, dict]:
    coin_id = resolve_coin_id(symbol)
    params = urlencode({
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "include_24hr_change": "true",
        "include_last_updated_at": "true",
    })
    payload, http_meta = request_json(f"https://api.coingecko.com/api/v3/simple/price?{params}", timeout=8, attempts=2)
    item = payload.get(coin_id) if isinstance(payload, dict) else None
    if not item or item.get("usd") is None:
        raise FinanceError("NO_DATA", f"CoinGecko returned no data for {symbol}", "coingecko")
    price = item["usd"]
    change_pct = item.get("usd_24h_change")
    changed = price / (1 + change_pct / 100) if change_pct not in (None, -100) else None
    previous = changed if changed else None
    timestamp = item.get("last_updated_at")
    as_of = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if timestamp else None
    return {
        "asset": {"market": "crypto", "symbol": symbol, "provider_symbol": coin_id, "name": symbol, "type": "stablecoin" if symbol in STABLECOINS else "crypto", "currency": "USD", "exchange": "aggregated", "timezone": "UTC"},
        "quote": {
            "price": price,
            "previous_close": previous,
            "change": price - previous if previous else None,
            "change_pct": change_pct,
            "open": None,
            "high": None,
            "low": None,
            "volume": item.get("usd_24h_vol"),
            "market_cap": item.get("usd_market_cap"),
            "market_state": "OPEN_24_7",
            "as_of": as_of,
            "source": "CoinGecko",
            "feed": "Public aggregated crypto feed",
            "realtime": True,
            "delayed_seconds": None,
        },
        "history": [],
    }, http_meta


def market_chart(symbol: str, days: int = 90) -> tuple[list[dict], dict]:
    coin_id = resolve_coin_id(symbol)
    days = max(1, min(days, 365))
    request_params = {"vs_currency": "usd", "days": str(days)}
    if days > 5:
        request_params["interval"] = "daily"
    params = urlencode(request_params)
    payload, http_meta = request_json(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?{params}", timeout=10, attempts=2)
    prices = payload.get("prices") if isinstance(payload, dict) else None
    volumes = payload.get("total_volumes") if isinstance(payload, dict) else []
    if not prices:
        raise FinanceError("NO_DATA", f"CoinGecko returned no history for {symbol}", "coingecko")
    volume_map = {int(v[0]): v[1] for v in volumes}
    bars = []
    for stamp_ms, price in prices:
        stamp = int(stamp_ms / 1000)
        bars.append({
            "time": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "timestamp": stamp,
            "open": price, "high": price, "low": price, "close": price,
            "adjusted_close": price, "volume": volume_map.get(int(stamp_ms), 0),
        })
    return bars, http_meta
