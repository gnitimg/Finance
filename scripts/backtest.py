from __future__ import annotations

import math

from .monitoring import ml
from .monitoring.ml import MODEL_PROFILES, instrument_profile

# Per-market trading rules. Fees are fractions of turnover. T+1 markets may
# not sell shares bought on the same trading day; A-shares additionally trade
# in 100-share round lots and carry a sell-only stamp duty.
MARKET_RULES = {
    "cn": {"t_plus": 1, "commission": 0.0005, "min_commission": 5.0, "stamp_sell": 0.0005, "lot": 100},
    "hk": {"t_plus": 0, "commission": 0.0015, "min_commission": 0.0, "stamp_sell": 0.001, "lot": 1},
    "us": {"t_plus": 0, "commission": 0.0, "min_commission": 0.0, "stamp_sell": 0.0, "lot": 1},
    "crypto": {"t_plus": 0, "commission": 0.001, "min_commission": 0.0, "stamp_sell": 0.0, "lot": 1},
    "etf": {"t_plus": 0, "commission": 0.0005, "min_commission": 5.0, "stamp_sell": 0.0, "lot": 100},
}
DEFAULT_THRESHOLD_GRID = (0.002, 0.005, 0.01, 0.02, 0.04)


def _bar_day(bar: dict) -> str:
    return str(bar.get("time", ""))[:10]


def _buy_cost(rules: dict, turnover: float) -> float:
    return max(turnover * rules["commission"], rules["min_commission"])


def _sell_cost(rules: dict, turnover: float) -> float:
    return max(turnover * rules["commission"], rules["min_commission"]) + turnover * rules["stamp_sell"]


def _quantity_for(rules: dict, cash: float, price: float) -> int:
    if rules["lot"] > 1:
        budget = cash / (1 + rules["commission"])
        return max(0, int(budget / price // rules["lot"]) * rules["lot"])
    return max(0, int(cash / (1 + rules["commission"]) / price))


def _trade(bars, samples, weights, means, scales, profile, rules, capital, threshold, start, end, horizon):
    """Walk one segment chronologically, trading the ensemble's signal.

    Signals form at a bar's close and execute at the next bar's open, matching
    what a live follower of this model could actually do.
    """
    cash = capital
    shares = 0
    buy_day = None
    buy_bar_index = -1
    cost_basis = 0.0
    fees_paid = 0.0
    trades = []
    equity = []
    in_market = 0
    known = list(samples[:start])
    component_errors = {"ridge": None, "analogue": None, "trend": None}
    raw_returns = []
    actual_returns = []
    config = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])
    fit_ridge = config["ridge"]
    for sample_index in range(start, end):
        sample = samples[sample_index]
        next_bar = bars[sample_index + 21] if sample_index + 21 < len(bars) else None
        if next_bar is None or not next_bar.get("open"):
            break
        row = ml._vector(sample["features"], means, scales)
        ridge_return = ml._predict(weights, row)
        analogue_return = ml._analogue_return(known, sample["features"], means, scales)
        trend_return = ml._trend_return(sample["features"], horizon, profile)
        blend = ml._component_weights(component_errors, profile)
        raw_return, components = ml._ensemble_return(ridge_return, analogue_return, trend_return, blend)
        raw_return, _guarded = ml._regime_guard(raw_return, sample["features"], horizon, profile)
        shrinkage = ml._return_shrinkage(raw_returns, actual_returns, profile)
        predicted = max(-0.35, min(0.35, raw_return * shrinkage))

        origin_bar = bars[sample_index + 20]
        origin_day = _bar_day(origin_bar)
        next_day = _bar_day(next_bar)
        execute_price = float(next_bar["open"])
        t_plus_ok = buy_day is None or next_day > buy_day
        bars_held = sample_index + 21 - buy_bar_index

        if shares == 0 and predicted >= threshold:
            quantity = _quantity_for(rules, cash, execute_price)
            turnover = quantity * execute_price
            cost = _buy_cost(rules, turnover)
            if quantity > 0 and turnover + cost <= cash:
                cash -= turnover + cost
                fees_paid += cost
                shares = quantity
                buy_day = next_day
                buy_bar_index = sample_index + 21
                cost_basis = turnover + cost
                trades.append({"side": "buy", "quantity": quantity, "price": round(execute_price, 4), "fees": round(cost, 2), "predicted_pct": round(predicted * 100, 3), "time": next_bar.get("time")})
        elif shares > 0 and t_plus_ok and (predicted <= -threshold or bars_held >= horizon + 2):
            turnover = shares * execute_price
            cost = _sell_cost(rules, turnover)
            pnl = turnover - cost - cost_basis
            cash += turnover - cost
            fees_paid += cost
            trades.append({"side": "sell", "quantity": shares, "price": round(execute_price, 4), "fees": round(cost, 2), "pnl": round(pnl, 2), "predicted_pct": round(predicted * 100, 3), "time": next_bar.get("time")})
            shares = 0
            cost_basis = 0.0

        close_price = float(origin_bar["close"])
        equity.append(cash + shares * close_price)
        in_market += 1 if shares else 0

        # Consume the realized outcome exactly like the forecast validation walk.
        alpha = 0.12
        residual = predicted - sample["target"]
        rate = 0.025 / math.sqrt(sample_index - start + 1)
        for index in range(len(weights)):
            penalty = 0.001 * weights[index] if index else 0.0
            weights[index] -= rate * (residual * row[index] + penalty)
        for name, value in components.items():
            component_error = abs(value - sample["target"])
            previous = component_errors.get(name)
            component_errors[name] = component_error if previous is None else (1 - alpha) * previous + alpha * component_error
        actual_returns.append(sample["target"])
        raw_returns.append(raw_return)
        known.append(sample)
    final_price = float(bars[-1]["close"]) if bars else 0.0
    equity.append(cash + shares * final_price)
    return {"cash": cash, "shares": shares, "cost_basis": cost_basis, "equity": equity, "trades": trades, "fees_paid": round(fees_paid, 2), "in_market": in_market, "bars": len(equity) - 1}


def _segment(bars, samples, profile, rules, capital, threshold, fit_end, trade_start, trade_end, horizon):
    config = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])
    ridge = config["ridge_small"] if fit_end < 40 else config["ridge"]
    weights, means, scales = ml._fit(samples[:fit_end], ridge=ridge)
    return _trade(bars, samples, weights, means, scales, profile, rules, capital, threshold, trade_start, trade_end, horizon)


def _report(bars, capital, state, trades, trade_start, threshold, profile, horizon):
    equity = state["equity"]
    final = equity[-1] if equity else capital
    peak = capital
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    sell_pnls = [trade.get("pnl") or 0.0 for trade in trades if trade["side"] == "sell"]
    start_close = float(bars[trade_start + 20]["close"])
    end_close = float(bars[-1]["close"])
    wins = sum(1 for pnl in sell_pnls if pnl > 0)
    return {
        "status": "ready",
        "profile": profile,
        "horizon_bars": horizon,
        "capital": capital,
        "threshold_pct": round(threshold * 100, 2),
        "final_equity": round(final, 2),
        "return_pct": round((final / capital - 1) * 100, 3),
        "buy_hold_return_pct": round((end_close / start_close - 1) * 100, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "trades": len(trades),
        "closed_trades": len(sell_pnls),
        "win_rate_pct": round(wins / len(sell_pnls) * 100, 1) if sell_pnls else None,
        "fees_paid": state["fees_paid"],
        "exposure_pct": round(state["in_market"] / max(1, state["bars"]) * 100, 1),
        "open_position": bool(state["shares"]),
        "recent_trades": trades[-20:],
    }


def run(bars: list[dict], market: str, symbol: str, interval: str = "1d", capital: float = 1_000_000.0, threshold: float = 0.01, horizon: int | None = None, grid: tuple = DEFAULT_THRESHOLD_GRID, iterate: bool = True) -> dict:
    if len(bars) < 120:
        return {"status": "insufficient_data", "bars": len(bars), "required": 120}
    profile = instrument_profile(market, symbol, None)
    rules = MARKET_RULES.get(market) or MARKET_RULES["us"]
    resolved_horizon = horizon or ml.adaptive_horizon(market, symbol, interval, None)
    samples = ml.build_samples(bars, resolved_horizon)
    if len(samples) < 120:
        return {"status": "insufficient_data", "samples": len(samples), "required": 120}

    split = int(len(samples) * 0.6)
    half = (len(samples) - split) // 2
    select_range = (split, split + half)
    verify_range = (split + half, len(samples))

    if not iterate:
        state = _segment(bars, samples, profile, rules, capital, threshold, verify_range[0], verify_range[0], verify_range[1], resolved_horizon)
        return _report(bars, capital, state, state["trades"], verify_range[0], threshold, profile, resolved_horizon)

    best = None
    grid_results = []
    for thr in grid:
        state = _segment(bars, samples, profile, rules, capital, thr, select_range[0], select_range[0], select_range[1], resolved_horizon)
        final_equity = state["equity"][-1] if state["equity"] else capital
        grid_results.append({"threshold_pct": round(thr * 100, 2), "selection_return_pct": round((final_equity / capital - 1) * 100, 3), "trades": len(state["trades"])})
        score = final_equity
        if best is None or score > best["score"]:
            best = {"threshold": thr, "score": score}
    chosen = best["threshold"] if best else threshold
    verify = _segment(bars, samples, profile, rules, capital, chosen, verify_range[0], verify_range[0], verify_range[1], resolved_horizon)
    report = _report(bars, capital, verify, verify["trades"], verify_range[0], chosen, profile, resolved_horizon)
    report["iteration"] = {"selected_threshold_pct": round(chosen * 100, 2), "grid_results": grid_results, "selection": "profit on first half of holdout", "verification": "second half of holdout, refit on everything before it"}
    return report
