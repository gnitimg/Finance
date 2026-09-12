#!/usr/bin/env python3
"""Background calibration sweep.

Runs the deterministic trainer over the configured watchlist so online
calibration, component weights, and shrinkage keep maturing even when no
visitor triggers an analysis. Designed for a systemd timer; every failure
degrades to the deterministic engine untouched.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.config import read_json
from scripts.finance import train_models
from scripts.providers.eastmoney import daily_flow, refresh_flow_today, sector_context
from scripts.symbols import normalize

INTRADAY_ASSETS = 4
CN_ZONE = ZoneInfo("Asia/Shanghai")


def restricted_window(now: datetime) -> bool:
    """Heavy historical fetches stay out of A-share trading and the 30-minute
    pre-open window; realtime latency matters more than calibration then."""
    if now.weekday() >= 5:
        return False
    minute_of_day = now.hour * 60 + now.minute
    return 8 * 60 + 55 <= minute_of_day <= 15 * 60 + 5


def summarize(result: dict, label: str) -> None:
    for item in result.get("data", {}).get("items") or []:
        asset = item.get("asset") or {}
        evaluation = item.get("evaluation") or {}
        confidence = item.get("confidence") or {}
        print(
            f"[{label}] {asset.get('market')}:{asset.get('symbol')} "
            f"skill={evaluation.get('skill_vs_no_change_pct')} "
            f"dir={evaluation.get('directional_accuracy')} "
            f"phase={evaluation.get('phase_lag_bars')} "
            f"confidence={confidence.get('score')} capped={confidence.get('capped_by_validation')}",
            flush=True,
        )
    for failure in result.get("data", {}).get("failures") or []:
        print(f"[{label}] failure {failure.get('market')}:{failure.get('symbol')} {failure.get('message')}", flush=True)


def main() -> int:
    watchlist = read_json("watchlist.json").get("assets") or []
    assets = [f"{item.get('market', 'auto')}:{item.get('symbol')}" for item in watchlist if item.get("symbol")][:8]
    if not assets:
        print("watchlist empty; nothing to train", flush=True)
        return 0
    normalized_assets = []
    for item in assets:
        market, symbol = item.split(":", 1) if ":" in item else ("auto", item)
        market, symbol = normalize(market, symbol)
        normalized_assets.append((market, symbol))

    now = datetime.now(CN_ZONE)
    if restricted_window(now):
        # The delay endpoint provides today's lightweight running flow row.
        # Historical flow, board K-lines, and model sweeps wait until after the
        # close so the public feed cannot slow interactive users.
        for market, symbol in normalized_assets:
            if market == "cn":
                refresh_flow_today(market, symbol)
        print("[context] A-share session active; refreshed live flow and deferred heavy training", flush=True)
        return 0

    # Pre-warm sequentially. East Money previously rate-limited bursty parallel
    # tests, while a single pass every 30 minutes stays within the intended
    # request budget. Failures only remove optional context.
    for market, symbol in normalized_assets:
        if market not in {"cn", "hk", "us"}:
            continue
        try:
            daily_flow(market, symbol)
        except Exception as exc:  # optional context must never abort calibration
            print(f"[context] {market}:{symbol} flow unavailable: {type(exc).__name__}", flush=True)
        if market == "cn":
            try:
                sector_context(market, symbol)
            except Exception as exc:
                print(f"[context] {market}:{symbol} sector unavailable: {type(exc).__name__}", flush=True)
    daily = train_models(assets, "3mo", "1d")
    summarize(daily, "1d")
    intraday = train_models(assets[:INTRADAY_ASSETS], "1mo", "5m")
    summarize(intraday, "5m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
