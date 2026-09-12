#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyzers.position import analyze_position
from scripts.backtest import run as run_backtest
from scripts.analyzers.company_risk import analyze as company_risk_analysis
from scripts.analyzers.market_sentiment import analyze as market_sentiment_analysis
from scripts.analyzers.stablecoin import analyze as stablecoin_analysis
from scripts.analyzers.technical import analyze as technical_analysis
from scripts.cache import CACHE
from scripts.config import env_bool, load_dotenv, read_json
from scripts.models import FinanceError, clean_json, utc_now
from scripts.monitoring.ml import adaptive_horizon, flow_series_by_index, forecast
from scripts.monitoring.scanner import scan as monitor_scan
from scripts.news.service import cached_news, get_news
from scripts.providers.eastmoney import cached_daily_flow, cached_market_reference, cached_risk_reports, cached_sector_context, risk_reports, sector_context
from scripts.providers.service import history, quote
from scripts.providers.tencent import order_book_cn
from scripts.routing import classify
from scripts.specialist.client import analyze as specialist_analyze
from scripts.symbols import STABLECOINS, normalize


def request_id() -> str:
    return "fin_" + uuid.uuid4().hex[:16]


def envelope(operation: str) -> dict:
    return {"success": True, "finance_request_id": request_id(), "operation": operation, "generated_at": utc_now(), "data": None, "warnings": [], "errors": [], "timing": {"data_ms": 0, "analysis_ms": 0, "specialist_ms": 0, "total_ms": 0}}


def _bar_epoch(bar: dict) -> float:
    timestamp = bar.get("timestamp")
    if timestamp is not None:
        numeric = float(timestamp)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    return datetime.fromisoformat(str(bar["time"]).replace("Z", "+00:00")).timestamp()


def align_model_history(context_history: list[dict], display_history: list[dict], interval: str) -> list[dict]:
    """Keep long training context, but make its live edge identical to the chart."""
    if not context_history:
        return list(display_history)
    if not display_history:
        return list(context_history)
    latest_visible = display_history[-1]
    visible_epoch = _bar_epoch(latest_visible)
    aligned = [bar for bar in context_history if _bar_epoch(bar) <= visible_epoch]
    if not aligned:
        return list(display_history)
    interval_seconds = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "90m": 5400, "1d": 86400}.get(interval, 300)
    if abs(_bar_epoch(aligned[-1]) - visible_epoch) < interval_seconds:
        aligned[-1] = latest_visible
    elif _bar_epoch(aligned[-1]) < visible_epoch:
        aligned.append(latest_visible)
    return aligned


def latest_market_session(bars: list[dict], zone_name: str | None) -> list[dict]:
    """Return only the latest exchange-local trading date for a 1D chart."""
    if not bars:
        return []
    try:
        zone = ZoneInfo(zone_name or "UTC")
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")

    def session_day(bar: dict):
        return datetime.fromtimestamp(_bar_epoch(bar), tz=zone).date()

    latest_day = session_day(bars[-1])
    return [bar for bar in bars if session_day(bar) == latest_day]


def quote_asset(market: str, symbol: str) -> dict:
    started = time.perf_counter()
    result = envelope("quote")
    result["data"] = quote(market, symbol)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    result["timing"]["data_ms"] = elapsed
    result["timing"]["total_ms"] = elapsed
    result["routing"] = {"level": "L0", "specialist_requested": False, "specialist_used": False, "reason": "fact_query", "specialist_model": None}
    return result


def _sector_view(payload: dict | None) -> dict | None:
    if not payload or not payload.get("industry"):
        return None
    return {
        "industry": payload.get("industry"),
        "board_code": payload.get("board_code"),
        "board_name": payload.get("board_name"),
        "board_change_pct": payload.get("board_change_pct_now"),
        "board_main_net": payload.get("board_main_net"),
        "board_return_1": payload.get("board_return_1"),
        "board_momentum_20": payload.get("board_momentum_20"),
        "index_return_1": payload.get("index_return_1"),
        "index_momentum_20": payload.get("index_momentum_20"),
        "top_boards": payload.get("top_boards") or [],
        "source": payload.get("source") or "East Money",
        "as_of": payload.get("as_of"),
        "cache": payload.get("cache") or {},
    }


def analyze_asset(market: str, symbol: str, range_name: str = "3mo", interval: str = "1d", shares: float | None = None, cost: float | None = None, use_ml: bool = True) -> dict:
    started = time.perf_counter()
    result = envelope("analyze")
    data_started = time.perf_counter()
    market, symbol = normalize(market, symbol)
    shared_intraday_context = range_name == "1d" and interval == "5m" and market in {"cn", "hk", "us", "etf", "fund"}
    market_data = history(market, symbol, "5d" if shared_intraday_context else range_name, interval)
    fetched_history = market_data.get("history") or []
    display_history = latest_market_session(fetched_history, (market_data.get("asset") or {}).get("timezone")) if shared_intraday_context else fetched_history
    if shared_intraday_context:
        market_data = {**market_data, "history": display_history}
    ml_history = fetched_history if shared_intraday_context and use_ml else display_history
    related_content = cached_news(market, symbol) if use_ml else None
    ml_context = {
        "range": "5d" if shared_intraday_context else range_name,
        "interval": interval,
        "bars": len(fetched_history) if shared_intraday_context else len(ml_history),
        "extended": shared_intraday_context,
        "live_edge_aligned": shared_intraday_context,
    }
    if use_ml:
        # Intraday keeps the fast 5-day context so monitor scans stay quick; deep
        # intraday training is available on demand via `train --range 1mo`.
        context = None if shared_intraday_context else (("1y", "1d") if market == "crypto" else ("5y", "1d")) if interval == "1d" else None
        if context and context != (range_name, interval):
            try:
                context_data = history(market, symbol, context[0], context[1])
                context_history = context_data.get("history") or []
                if len(context_history) > len(ml_history):
                    ml_history = align_model_history(context_history, display_history, interval)
                    ml_context = {"range": context[0], "interval": context[1], "bars": len(ml_history), "extended": True, "live_edge_aligned": True}
            except FinanceError as exc:
                result["warnings"].append(f"Extended ML context unavailable: {exc.message}")
    data_ms = round((time.perf_counter() - data_started) * 1000, 2)
    analysis_started = time.perf_counter()
    technical = technical_analysis(display_history, market_data["quote"].get("price"), interval)
    market_sentiment = market_sentiment_analysis(display_history, market_data["quote"], technical, (related_content or {}).get("sentiment"), risk=(related_content or {}).get("company_risk"))
    market_sentiment["sources"] = [market_data["quote"].get("source"), "technical and anomaly engine"]
    if (related_content or {}).get("sentiment", {}).get("evidence_count"):
        market_sentiment["sources"].append("东方财富 / GDELT / Yahoo Finance related content")
    asset_type = (market_data.get("asset") or {}).get("type")
    to_session_close = range_name == "1d" and interval == "5m" and market in {"cn", "hk", "us", "etf", "fund"}
    horizon = adaptive_horizon(market, symbol, interval, asset_type)
    live_context = market_sentiment
    flow = None
    sector_payload = None
    if use_ml and market == "cn":
        # Interactive analysis never waits on East Money's board endpoints.
        # The optional context stage and the background trainer pre-warm this
        # cache, so switching symbols remains bounded by the primary feed.
        sector_payload = cached_sector_context(market, symbol)
        if not sector_payload and not env_bool("FINANCE_FAST_PATH", False):
            try:
                sector_payload = sector_context(market, symbol)
            except Exception as exc:
                result["warnings"].append(f"Sector context unavailable: {type(exc).__name__}")
    if use_ml and market in {"cn", "hk", "us"}:
        try:
            flow = cached_daily_flow(market, symbol)
            if flow:
                live_signal = flow_series_by_index([flow[-1]["date"]], flow)[-1]
                live_context = {**market_sentiment, "flow": {"zscore": live_signal[0], "trend_3d": live_signal[1]}}
        except (FinanceError, KeyError, ValueError) as exc:
            result["warnings"].append(f"Cached fund-flow features unavailable: {type(exc).__name__}")
        if market == "cn":
            try:
                book = order_book_cn(symbol)
                live_context = {**live_context, "book": {"imbalance": book["imbalance"], "active_buy_ratio": book["active_buy_ratio"]}}
            except FinanceError as exc:
                result["warnings"].append(f"Order-book signal unavailable: {exc.message}")
        if sector_payload:
            live_context = {**live_context, "sector": {"board_change_pct": sector_payload.get("board_change_pct_now"), "board_ma20_gap": sector_payload.get("board_ma20_gap")}}
        company_risk = (related_content or {}).get("company_risk") or {}
        if company_risk.get("detected_count"):
            live_context = {**live_context, "risk": {"priority": company_risk.get("priority_score"), "detected": company_risk.get("detected_count")}}
    market_ref = None
    if sector_payload and interval == "1d" and sector_payload.get("board_code"):
        market_ref = cached_market_reference(sector_payload.get("board_code"))
    ml = forecast(ml_history, market, symbol, interval, horizon, to_session_close=to_session_close, asset_type=asset_type, live_context=live_context, flow=flow, market_ref=market_ref) if use_ml else {"status": "disabled", "series": {"predicted": [], "actual": []}}
    ml["context"] = ml_context
    position = analyze_position(float(market_data["quote"]["price"]), shares, cost)
    company_risk = company_risk_analysis(
        (related_content or {}).get("items") or [],
        market=market,
        entity=(market_data.get("asset") or {}).get("name") or symbol,
        flow=flow,
        news_providers=(related_content or {}).get("providers_checked"),
        as_of=market_data["quote"].get("as_of"),
    )
    result["data"] = {**market_data, "technical": technical, "market_sentiment": market_sentiment, "company_risk": company_risk, "ml_forecast": ml, "position": position}
    sector_view = _sector_view(sector_payload)
    if sector_view:
        result["data"]["sector"] = sector_view
    result["warnings"].extend(market_data.get("warnings") or [])
    result["warnings"].append("Machine-learning confidence is historical calibration quality, not a probability or promise.")
    analysis_ms = round((time.perf_counter() - analysis_started) * 1000, 2)
    result["timing"].update({"data_ms": data_ms, "analysis_ms": analysis_ms, "total_ms": round((time.perf_counter() - started) * 1000, 2)})
    result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_analysis", "specialist_model": None, "specialist_recommended": bool((technical.get("signal_conflict") or {}).get("material"))}
    result["disclaimer"] = "For information and research only; not investment advice or a trade instruction."
    return result


def news_asset(market: str, symbol: str, limit: int = 12) -> dict:
    market, symbol = normalize(market, symbol)
    context = None
    context_warning = None
    try:
        context = history(market, symbol, "3mo", "1d")
    except FinanceError as exc:
        context_warning = f"Market anomaly context unavailable: {exc.message}"
    related_name = ((context or {}).get("asset") or {}).get("name")
    news = get_news(market, symbol, limit, related_name)
    if context:
        bars = context.get("history") or []
        quote_data = context.get("quote") or {}
        technical = technical_analysis(bars, quote_data.get("price"), "1d")
        news["market_sentiment"] = market_sentiment_analysis(bars, quote_data, technical, news.get("sentiment"), risk=news.get("company_risk"))
        news["market_sentiment"]["sources"] = [quote_data.get("source"), "Yahoo Finance historical bars", "东方财富 / GDELT / Yahoo Finance related content"]
    flow = cached_daily_flow(market, symbol) if market in {"cn", "hk", "us"} else None
    news["company_risk"] = company_risk_analysis(
        news.get("items") or [],
        market=market,
        entity=related_name or symbol,
        flow=flow,
        news_providers=news.get("providers_checked"),
        as_of=news.get("generated_at"),
        structured=risk_reports(market, symbol, security_name=related_name or symbol) or cached_risk_reports(market, symbol),
    )
    if market == "cn":
        try:
            sector_view = _sector_view(sector_context(market, symbol))
            if sector_view:
                news["sector"] = sector_view
        except Exception as exc:  # optional context must not hide verified news
            news.setdefault("warnings", []).append(f"Sector context unavailable: {type(exc).__name__}")
    if context_warning:
        news.setdefault("warnings", []).append(context_warning)
    return news


def risk_context_asset(market: str, symbol: str, limit: int = 20) -> dict:
    """Refresh watchlist risk evidence without loading charts or a model."""
    market, symbol = normalize(market, symbol)
    cached_quote, _cache_meta = CACHE.get(f"quote:{market}:{symbol}", 0, 86_400)
    related_name = ((cached_quote or {}).get("asset") or {}).get("name")
    news = get_news(market, symbol, limit, related_name)
    flow = cached_daily_flow(market, symbol) if market in {"cn", "hk", "us"} else None
    news["company_risk"] = company_risk_analysis(
        news.get("items") or [],
        market=market,
        entity=related_name or symbol,
        flow=flow,
        news_providers=news.get("providers_checked"),
        as_of=news.get("generated_at"),
        structured=risk_reports(market, symbol, security_name=related_name or symbol) or cached_risk_reports(market, symbol),
    )
    return news


def monitor_assets(assets: list[str] | None = None, forecast_pct: float = 0.7, price_change_pct: float = 2.0, volume_ratio: float = 1.8) -> dict:
    started = time.perf_counter()
    result = envelope("monitor")
    if not assets:
        configured = read_json("watchlist.json").get("assets") or []
        assets = [f"{item.get('market', 'auto')}:{item.get('symbol')}" for item in configured if item.get("symbol")]
    normalized = []
    seen = set()
    for item in assets[:8]:
        market, symbol = item.split(":", 1) if ":" in item else ("auto", item)
        pair = normalize(market, symbol)
        if pair not in seen:
            seen.add(pair)
            normalized.append(pair)
    if not normalized:
        raise ValueError("monitor requires at least one asset")
    thresholds = {"forecast_pct": forecast_pct, "price_change_pct": price_change_pct, "volume_ratio": volume_ratio}
    completed = {}
    failures = []

    def inspect(index: int, market: str, symbol: str):
        analysis = analyze_asset(market, symbol, "1d", "5m")
        return index, monitor_scan(analysis["data"], thresholds)

    with ThreadPoolExecutor(max_workers=min(4, len(normalized))) as pool:
        futures = {pool.submit(inspect, index, market, symbol): (market, symbol) for index, (market, symbol) in enumerate(normalized)}
        for future in as_completed(futures):
            market, symbol = futures[future]
            try:
                index, item = future.result()
                completed[index] = item
            except Exception as exc:
                failures.append({"market": market, "symbol": symbol, "message": str(exc)[:180]})
    items = [completed[index] for index in sorted(completed)]
    result["data"] = {"items": items, "alerts": [alert for item in items for alert in item["alerts"]], "thresholds": thresholds, "refresh_seconds": 8, "failures": failures}
    if failures:
        result["warnings"].append(f"{len(failures)} monitored asset(s) were temporarily unavailable.")
    result["success"] = bool(items)
    if not items:
        result["errors"].append({"code": "NO_MONITOR_DATA", "message": "No monitored asset returned usable data"})
    result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_watchlist_monitor", "specialist_model": None}
    return result


def train_models(assets: list[str], range_name: str = "3mo", interval: str = "1d") -> dict:
    started = time.perf_counter()
    result = envelope("train")
    normalized = []
    seen = set()
    for item in assets[:12]:
        market, symbol = item.split(":", 1) if ":" in item else ("auto", item)
        pair = normalize(market, symbol)
        if pair not in seen:
            seen.add(pair)
            normalized.append(pair)
    if not normalized:
        raise ValueError("train requires at least one asset")
    completed = {}
    failures = []

    def fit(index: int, market: str, symbol: str):
        analysis = analyze_asset(market, symbol, range_name, interval)
        data = analysis["data"]
        forecast_data = data.get("ml_forecast") or {}
        return index, {
            "asset": data.get("asset"),
            "status": forecast_data.get("status"),
            "horizon_label": forecast_data.get("horizon_label"),
            "profile": (forecast_data.get("ensemble") or {}).get("profile"),
            "training": forecast_data.get("training"),
            "evaluation": forecast_data.get("evaluation"),
            "confidence": forecast_data.get("confidence"),
            "publishable": (forecast_data.get("next_forecast") or {}).get("publishable"),
            "state_scope": f"{market}:{symbol}:{interval}",
        }

    with ThreadPoolExecutor(max_workers=min(4, len(normalized))) as pool:
        futures = {pool.submit(fit, index, market, symbol): (market, symbol) for index, (market, symbol) in enumerate(normalized)}
        for future in as_completed(futures):
            market, symbol = futures[future]
            try:
                index, item = future.result()
                completed[index] = item
            except Exception as exc:
                failures.append({"market": market, "symbol": symbol, "message": str(exc)[:180]})
    items = [completed[index] for index in sorted(completed)]
    result["data"] = {"interval": interval, "items": items, "failures": failures}
    result["success"] = bool(items)
    if not items:
        result["errors"].append({"code": "NO_TRAIN_DATA", "message": "No asset returned usable training data"})
    result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_model_training", "specialist_model": None}
    return result


def workflow(assets: list[str], query_text: str, specialist_mode: str = "auto", range_name: str = "3mo", interval: str = "1d") -> dict:
    started = time.perf_counter()
    parsed = []
    for item in assets:
        if ":" not in item:
            parsed.append(normalize("auto", item))
        else:
            market, symbol = item.split(":", 1)
            parsed.append(normalize(market, symbol))
    route = classify(query_text, len(parsed))
    if specialist_mode == "force" and route["level"] != "L0":
        route.update({"level": "L2", "specialist_recommended": True, "reason": "specialist_forced"})
    result = envelope("workflow")
    deterministic = []
    data_ms = analysis_ms = 0.0
    for market, symbol in parsed:
        if route["level"] == "L0":
            item = quote_asset(market, symbol)
        elif market == "crypto" and symbol in STABLECOINS:
            item_started = time.perf_counter()
            item = envelope("stablecoin")
            item["data"] = stablecoin_analysis(symbol)
            item["timing"]["data_ms"] = round((time.perf_counter() - item_started) * 1000, 2)
        else:
            item = analyze_asset(market, symbol, range_name, interval)
        data_ms += item["timing"].get("data_ms", 0)
        analysis_ms += item["timing"].get("analysis_ms", 0)
        deterministic.append(item["data"])

    result["data"] = {"assets": deterministic, "query": query_text}
    result["routing"] = {
        **route,
        "specialist_requested": False,
        "specialist_used": False,
        "specialist_model": None,
        "mode": specialist_mode,
    }
    if route["causal_reasoning"] and not route["causal_data_available"]:
        result["warnings"].append("Current data confirms price, volume, and technical changes only; it cannot confirm a specific cause without news or event evidence.")

    should_call = route["level"] == "L2" and route["specialist_recommended"] and specialist_mode != "off" and not route["causal_reasoning"]
    if should_call:
        result["routing"]["specialist_requested"] = True
        compact_assets = []
        for item in deterministic:
            compact_assets.append({key: item.get(key) for key in ("asset", "quote", "technical", "risk", "position") if item.get(key) is not None})
        specialist_started = time.perf_counter()
        try:
            specialist, metadata = specialist_analyze({"task": route["reason"], "assets": compact_assets, "analysis_goal": query_text, "warnings": result["warnings"], "source_quality": [{"source": (item.get("quote") or {}).get("source"), "as_of": (item.get("quote") or {}).get("as_of")} for item in deterministic]})
            result["data"]["specialist"] = specialist
            result["routing"].update({"specialist_used": True, "specialist_model": metadata["model"], "specialist_provider": metadata["provider"]})
        except FinanceError as exc:
            result["errors"].append(exc.as_dict())
            result["warnings"].append("Optional specialist was unavailable; deterministic market analysis is still complete.")
        result["timing"]["specialist_ms"] = round((time.perf_counter() - specialist_started) * 1000, 2)
    result["timing"].update({"data_ms": data_ms, "analysis_ms": analysis_ms, "total_ms": round((time.perf_counter() - started) * 1000, 2)})
    result["disclaimer"] = "For information and research only; not investment advice or a trade instruction."
    return result


def health() -> dict:
    started = time.perf_counter()
    result = envelope("health")
    providers = {}
    for name, market, symbol in [("sina", "cn", "601619"), ("yahoo", "us", "NVDA"), ("coingecko", "crypto", "USDT")]:
        check_started = time.perf_counter()
        try:
            data = quote(market, symbol)
            providers[name] = {"ok": True, "latency_ms": round((time.perf_counter() - check_started) * 1000, 2), "as_of": data["quote"].get("as_of"), "cached": data["cache"].get("cached"), "stale": data["cache"].get("stale")}
        except Exception as exc:
            providers[name] = {"ok": False, "latency_ms": round((time.perf_counter() - check_started) * 1000, 2), "error": str(exc)[:180]}
    result["data"] = {"engine": "python-deterministic", "python": sys.version.split()[0], "providers": providers, "cache_entries": CACHE.count(), "llm_policy": "L2-only; disabled for quotes, indicators, monitoring, and ML"}
    result["success"] = any(item["ok"] for item in providers.values())
    result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-skill", description="Verified deterministic finance engine")
    sub = parser.add_subparsers(dest="command", required=True)
    def pretty(command):
        command.add_argument("--pretty", action="store_true")
    quote_cmd = sub.add_parser("quote")
    quote_cmd.add_argument("--market", default="auto")
    quote_cmd.add_argument("--symbol", required=True)
    pretty(quote_cmd)
    analyze_cmd = sub.add_parser("analyze")
    analyze_cmd.add_argument("--market", default="auto")
    analyze_cmd.add_argument("--symbol", required=True)
    analyze_cmd.add_argument("--range", dest="range_name", default="3mo")
    analyze_cmd.add_argument("--interval", default="1d")
    analyze_cmd.add_argument("--position-shares", type=float)
    analyze_cmd.add_argument("--position-cost", type=float)
    analyze_cmd.add_argument("--no-ml", action="store_true")
    pretty(analyze_cmd)
    stable_cmd = sub.add_parser("stablecoin")
    stable_cmd.add_argument("--symbol", required=True)
    pretty(stable_cmd)
    flow_cmd = sub.add_parser("workflow")
    flow_cmd.add_argument("--asset", action="append", required=True)
    flow_cmd.add_argument("--query", required=True)
    flow_cmd.add_argument("--specialist", choices=("off", "auto", "force"), default="auto")
    flow_cmd.add_argument("--range", dest="range_name", default="3mo")
    flow_cmd.add_argument("--interval", default="1d")
    pretty(flow_cmd)
    news_cmd = sub.add_parser("news")
    news_cmd.add_argument("--market", default="auto")
    news_cmd.add_argument("--symbol", required=True)
    news_cmd.add_argument("--limit", type=int, default=12)
    pretty(news_cmd)
    risk_context_cmd = sub.add_parser("risk-context")
    risk_context_cmd.add_argument("--market", required=True)
    risk_context_cmd.add_argument("--symbol", required=True)
    risk_context_cmd.add_argument("--limit", type=int, default=20)
    pretty(risk_context_cmd)
    bt_cmd = sub.add_parser("backtest")
    bt_cmd.add_argument("--market", default="auto")
    bt_cmd.add_argument("--symbol", required=True)
    bt_cmd.add_argument("--range", dest="range_name", default="5y")
    bt_cmd.add_argument("--interval", default="1d")
    bt_cmd.add_argument("--capital", type=float, default=1_000_000)
    bt_cmd.add_argument("--threshold", type=float, default=0.01)
    bt_cmd.add_argument("--no-iterate", action="store_true")
    pretty(bt_cmd)
    train_cmd = sub.add_parser("train")
    train_cmd.add_argument("--asset", action="append", required=True)
    train_cmd.add_argument("--range", dest="range_name", default="3mo")
    train_cmd.add_argument("--interval", default="1d")
    pretty(train_cmd)
    monitor_cmd = sub.add_parser("monitor")
    monitor_cmd.add_argument("--asset", action="append")
    monitor_cmd.add_argument("--forecast-pct", type=float, default=0.7)
    monitor_cmd.add_argument("--price-change-pct", type=float, default=2.0)
    monitor_cmd.add_argument("--volume-ratio", type=float, default=1.8)
    pretty(monitor_cmd)
    health_cmd = sub.add_parser("health")
    pretty(health_cmd)
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    started = time.perf_counter()
    try:
        if args.command == "quote":
            result = quote_asset(args.market, args.symbol)
        elif args.command == "analyze":
            result = analyze_asset(args.market, args.symbol, args.range_name, args.interval, args.position_shares, args.position_cost, not args.no_ml)
        elif args.command == "stablecoin":
            result = envelope("stablecoin")
            result["data"] = stablecoin_analysis(args.symbol)
            result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
            result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_stablecoin_risk", "specialist_model": None}
        elif args.command == "workflow":
            result = workflow(args.asset, args.query, args.specialist, args.range_name, args.interval)
        elif args.command == "news":
            result = envelope("news")
            result["data"] = news_asset(args.market, args.symbol, args.limit)
            result["warnings"].extend(result["data"].get("warnings") or [])
            result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
            result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_news_sentiment", "specialist_model": None}
        elif args.command == "risk-context":
            result = envelope("risk-context")
            result["data"] = risk_context_asset(args.market, args.symbol, args.limit)
            result["warnings"].extend(result["data"].get("warnings") or [])
            result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
            result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_background_risk_context", "specialist_model": None}
        elif args.command == "monitor":
            result = monitor_assets(args.asset, args.forecast_pct, args.price_change_pct, args.volume_ratio)
        elif args.command == "train":
            result = train_models(args.asset, args.range_name, args.interval)
        elif args.command == "backtest":
            bt_market, bt_symbol = normalize(args.market, args.symbol)
            bt_data = history(bt_market, bt_symbol, args.range_name, args.interval)
            bt_result = run_backtest(bt_data.get("history") or [], bt_market, bt_symbol, args.interval, capital=args.capital, threshold=args.threshold, iterate=not args.no_iterate)
            result = envelope("backtest")
            result["data"] = {"asset": bt_data.get("asset"), "rules": bt_result}
            result["warnings"].extend(bt_data.get("warnings") or [])
            result["routing"] = {"level": "L1", "specialist_requested": False, "specialist_used": False, "reason": "deterministic_backtest", "specialist_model": None}
            result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
        else:
            result = health()
    except (FinanceError, ValueError) as exc:
        error = exc.as_dict() if isinstance(exc, FinanceError) else {"code": "INVALID_INPUT", "message": str(exc)}
        result = envelope(args.command)
        result.update({"success": False, "errors": [error]})
        result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    except Exception as exc:
        result = envelope(args.command)
        result.update({"success": False, "errors": [{"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"}]})
        result["timing"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    print(json.dumps(clean_json(result), ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None, allow_nan=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
