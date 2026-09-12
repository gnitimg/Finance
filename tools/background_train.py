#!/usr/bin/env python3
"""Background calibration sweep.

Runs the deterministic trainer over the configured watchlist so online
calibration, component weights, and shrinkage keep maturing even when no
visitor triggers an analysis. Designed for a systemd timer; every failure
degrades to the deterministic engine untouched.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.config import read_json
from scripts.finance import train_models
from scripts.providers.eastmoney import daily_flow
from scripts.symbols import normalize

INTRADAY_ASSETS = 4


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
    def refresh_flow(item: str) -> None:
        market, symbol = item.split(":", 1) if ":" in item else ("auto", item)
        market, symbol = normalize(market, symbol)
        if market not in {"cn", "hk", "us"}:
            return
        try:
            daily_flow(market, symbol)
        except Exception as exc:  # optional context must never abort calibration
            print(f"[context] {market}:{symbol} flow unavailable: {type(exc).__name__}", flush=True)

    with ThreadPoolExecutor(max_workers=min(4, len(assets))) as pool:
        list(pool.map(refresh_flow, assets))
    daily = train_models(assets, "3mo", "1d")
    summarize(daily, "1d")
    intraday = train_models(assets[:INTRADAY_ASSETS], "1mo", "5m")
    summarize(intraday, "5m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
