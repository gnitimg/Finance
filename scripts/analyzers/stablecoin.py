from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import read_json
from ..http_client import request_json
from ..models import FinanceError
from ..providers.service import quote
from ..symbols import STABLECOINS


def _dex(symbol: str, config: dict) -> dict:
    url = f"https://api.dexscreener.com/token-pairs/v1/{config['chain']}/{config['contract']}"
    payload, meta = request_json(url, timeout=8, attempts=2)
    if not isinstance(payload, list):
        raise FinanceError("INVALID_SCHEMA", "DexScreener response is not a list", "dexscreener")
    candidates = []
    allowed = set(config.get("counter_assets") or [])
    for pair in payload:
        base = (pair.get("baseToken") or {}).get("symbol", "").upper()
        counter = (pair.get("quoteToken") or {}).get("symbol", "").upper()
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        if base != symbol or counter not in allowed or liquidity < 10_000:
            continue
        try:
            price = float(pair.get("priceUsd"))
        except (TypeError, ValueError):
            continue
        candidates.append({"dex": pair.get("dexId"), "pair": f"{base}/{counter}", "price_usd": price, "liquidity_usd": liquidity, "url": pair.get("url")})
    candidates.sort(key=lambda item: item["liquidity_usd"], reverse=True)
    if not candidates:
        raise FinanceError("NO_DATA", "No verified liquid DEX pair found", "dexscreener")
    prices = [item["price_usd"] for item in candidates[:5]]
    return {"selected_pool": candidates[0], "verified_pools": candidates[:5], "cross_pool_spread_pct": (max(prices) / min(prices) - 1) * 100 if min(prices) else None, "provider_timing": meta}


def _supply(config: dict) -> dict:
    payload, meta = request_json(f"https://stablecoins.llama.fi/stablecoin/{config['defillama_id']}", timeout=8, attempts=2)
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    latest = tokens[-1] if tokens else None
    return {"current_supply": ((latest or {}).get("circulating") or {}).get("peggedUSD"), "provider_timing": meta}


def analyze(symbol: str) -> dict:
    symbol = symbol.upper()
    config = read_json("stablecoins.json").get(symbol, {})
    if symbol not in STABLECOINS and not config:
        raise FinanceError("UNSUPPORTED_SYMBOL", f"Stablecoin {symbol} is not recognized")
    market = quote("crypto", symbol)
    failures = []
    extras = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        if config.get("chain") and config.get("contract"):
            futures[pool.submit(_dex, symbol, config)] = "dexscreener"
        if config.get("defillama_id"):
            futures[pool.submit(_supply, config)] = "defillama"
        for future in as_completed(futures):
            name = futures[future]
            try:
                extras[name] = future.result()
            except Exception as exc:
                failures.append({"provider": name, "message": str(exc)[:240]})
    price = float(market["quote"]["price"])
    depeg_pct = abs(price - 1) * 100
    depeg_risk = min(100.0, depeg_pct / 2.0 * 100)
    pool = (extras.get("dexscreener") or {}).get("selected_pool") or {}
    liquidity = float(pool.get("liquidity_usd") or 0)
    liquidity_risk = 10 if liquidity >= 10_000_000 else 30 if liquidity >= 1_000_000 else 65 if liquidity >= 100_000 else None
    spread = (extras.get("dexscreener") or {}).get("cross_pool_spread_pct")
    spread_risk = min(100.0, (spread or 0) / 1.0 * 100)
    available = [(depeg_risk, 0.55)]
    if liquidity_risk is not None:
        available.append((liquidity_risk, 0.25))
    if spread is not None:
        available.append((spread_risk, 0.20))
    weight_sum = sum(weight for _, weight in available)
    risk_score = sum(value * weight for value, weight in available) / weight_sum
    level = "high" if risk_score >= 65 else "elevated" if risk_score >= 40 else "guarded" if risk_score >= 20 else "low"
    return {
        "asset": market["asset"], "quote": market["quote"],
        "risk": {"score": risk_score, "level": level, "depeg_pct": depeg_pct, "components": {"depeg": depeg_risk, "liquidity": liquidity_risk, "cross_pool_spread": spread_risk if spread is not None else None}},
        "coverage": {"level": "full" if extras.get("dexscreener") and extras.get("defillama") else "partial", "price": True, "dex_liquidity": bool(extras.get("dexscreener")), "supply": bool(extras.get("defillama"))},
        "dex_liquidity": extras.get("dexscreener"), "supply": extras.get("defillama"),
        "provider_failures": failures,
        "warnings": ["Risk score is a deterministic monitoring heuristic, not a default probability."] + (["Chain liquidity or supply coverage is partial for this stablecoin."] if not extras.get("dexscreener") or not extras.get("defillama") else []),
    }
