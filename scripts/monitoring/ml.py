from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import DATA_DIR

FEATURE_NAMES = [
    "return_1", "return_2", "return_3", "acceleration", "ma3_gap", "ma5_gap",
    "ma10_gap", "momentum_5", "trend_5", "volatility_5", "range_pct",
    "close_location", "volume_ratio", "return_5", "ma20_gap", "breakout_10",
    "volume_impulse", "volatility_ratio", "candle_streak", "up_ratio_10",
]
STATE_VERSION = 7
MIN_SAMPLES = 15
# Recency bounds keep the kNN analogue and the per-step path fits affordable
# on long training contexts without changing their short-memory character.
ANALOGUE_POOL = 500
PATH_FIT_WINDOW = 800
INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90}
MARKET_SESSIONS = {
    "cn": ("Asia/Shanghai", ((570, 690), (780, 900))),
    "hk": ("Asia/Hong_Kong", ((570, 720), (780, 960))),
    "us": ("America/New_York", ((570, 960),)),
    "etf": ("America/New_York", ((570, 960),)),
    "fund": ("America/New_York", ((570, 960),)),
}
MODEL_PROFILES = {
    "market": {"horizon": 3, "ridge": 2.5, "ridge_small": 7.0, "weights": {"ridge": .40, "analogue": .22, "trend": .38}, "velocity": .58, "context_scale": .72, "initial_shrink": .18},
    "cn_equity": {"horizon": 2, "ridge": 3.0, "ridge_small": 7.5, "weights": {"ridge": .34, "analogue": .20, "trend": .46}, "velocity": .66, "context_scale": .82, "initial_shrink": .16},
    "hk_equity": {"horizon": 2, "ridge": 2.8, "ridge_small": 7.0, "weights": {"ridge": .36, "analogue": .24, "trend": .40}, "velocity": .62, "context_scale": .78, "initial_shrink": .17},
    "us_equity": {"horizon": 3, "ridge": 2.5, "ridge_small": 6.5, "weights": {"ridge": .40, "analogue": .24, "trend": .36}, "velocity": .56, "context_scale": .76, "initial_shrink": .18},
    "etf": {"horizon": 3, "ridge": 3.2, "ridge_small": 7.5, "weights": {"ridge": .42, "analogue": .30, "trend": .28}, "velocity": .42, "context_scale": .58, "initial_shrink": .16},
    "fund": {"horizon": 2, "ridge": 4.5, "ridge_small": 9.0, "weights": {"ridge": .50, "analogue": .34, "trend": .16}, "velocity": .28, "context_scale": .42, "initial_shrink": .12},
    "future": {"horizon": 2, "ridge": 2.8, "ridge_small": 6.5, "weights": {"ridge": .32, "analogue": .20, "trend": .48}, "velocity": .72, "context_scale": .88, "initial_shrink": .20},
    "metal": {"horizon": 2, "ridge": 3.3, "ridge_small": 7.5, "weights": {"ridge": .36, "analogue": .30, "trend": .34}, "velocity": .48, "context_scale": .70, "initial_shrink": .16},
    "crypto": {"horizon": 3, "ridge": 3.0, "ridge_small": 7.0, "weights": {"ridge": .36, "analogue": .22, "trend": .42}, "velocity": .64, "context_scale": .84, "initial_shrink": .22},
    "stablecoin": {"horizon": 5, "ridge": 5.0, "ridge_small": 9.0, "weights": {"ridge": .44, "analogue": .34, "trend": .22}, "velocity": .12, "context_scale": .30, "initial_shrink": .58},
}


def instrument_profile(market: str, symbol: str, asset_type: str | None = None) -> str:
    normalized_type = str(asset_type or "").lower()
    if market == "crypto" and symbol.upper() in {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "PYUSD", "USDE", "USDS"}:
        return "stablecoin"
    if market in {"etf", "fund", "future", "metal"}:
        return market
    if normalized_type in {"etf", "fund", "future", "metal"}:
        return normalized_type
    if market in {"cn", "hk", "us"}:
        return f"{market}_equity"
    if market == "crypto":
        return "crypto"
    return "market"


def _horizon_label(interval: str, horizon: int) -> str:
    if interval in INTERVAL_MINUTES:
        total = INTERVAL_MINUTES[interval] * horizon
        if total < 60:
            return f"{total} 分钟"
        hours, remainder = divmod(total, 60)
        return f"{hours} 小时" if not remainder else f"{hours} 小时 {remainder} 分钟"
    if interval in {"1d", "1wk"}:
        return f"{horizon} 个交易{'日' if interval == '1d' else '周'}"
    return f"{horizon} 个 BAR"


def _features(bars: list[dict], index: int) -> list[float] | None:
    if index < 20:
        return None
    closes = [float(bars[i]["close"]) for i in range(index - 20, index + 1)]
    if any(value <= 0 for value in closes):
        return None
    volumes = [float(bars[i].get("volume") or 0) for i in range(index - 20, index + 1)]
    price = closes[-1]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean_return = sum(returns[-5:]) / 5
    variance = sum((item - mean_return) ** 2 for item in returns[-5:]) / 5
    mean_volume = sum(volumes[-6:-1]) / 5
    latest_bar = bars[index]
    high = float(latest_bar.get("high") or price)
    low = float(latest_bar.get("low") or price)
    price_range = max(high - low, 0.0)
    recent = closes[-5:]
    trend_slope = sum((position - 2) * value for position, value in enumerate(recent)) / 10 / price
    long_returns = returns[-15:]
    long_mean = sum(long_returns) / len(long_returns)
    long_variance = sum((item - long_mean) ** 2 for item in long_returns) / len(long_returns)
    recent_range = max(closes[-10:]) - min(closes[-10:])
    volume_ratio = volumes[-1] / mean_volume - 1 if mean_volume else 0.0
    up_flags = [1.0 if closes[i] > closes[i - 1] else -1.0 if closes[i] < closes[i - 1] else 0.0 for i in range(1, len(closes))]
    candle_streak = 0.0
    for flag in reversed(up_flags):
        if flag == 0:
            break
        if candle_streak == 0 or (flag > 0) == (candle_streak > 0):
            candle_streak += flag
        else:
            break
    up_ratio_10 = sum(1.0 for flag in up_flags[-10:] if flag > 0) / 10.0 - 0.5
    return [
        returns[-1],
        price / closes[-3] - 1,
        price / closes[-4] - 1,
        returns[-1] - returns[-2],
        price / (sum(closes[-3:]) / 3) - 1,
        price / (sum(closes[-5:]) / 5) - 1,
        price / (sum(closes[-10:]) / 10) - 1,
        price / closes[-6] - 1,
        trend_slope,
        math.sqrt(variance),
        price_range / price,
        (price - low) / price_range - 0.5 if price_range else 0.0,
        volume_ratio,
        price / closes[-6] - 1,
        price / (sum(closes[-20:]) / 20) - 1,
        (price - min(closes[-10:])) / recent_range - 0.5 if recent_range else 0.0,
        returns[-1] * (1 + max(-0.9, volume_ratio)),
        math.sqrt(variance) / max(math.sqrt(long_variance), 1e-8),
        candle_streak,
        up_ratio_10,
    ]


def build_samples(bars: list[dict], horizon: int = 3) -> list[dict]:
    samples = []
    for index in range(20, len(bars) - horizon):
        features = _features(bars, index)
        origin = float(bars[index]["close"])
        target = float(bars[index + horizon]["close"])
        if features is None or origin <= 0 or target <= 0:
            continue
        samples.append({
            "features": features,
            "target": math.log(target / origin),
            "origin_price": origin,
            "origin_time": bars[index]["time"],
            "target_price": target,
            "target_time": bars[index + horizon]["time"],
        })
    return samples


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            continue
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [augmented[row][i] - factor * augmented[column][i] for i in range(size + 1)]
    return [augmented[row][-1] for row in range(size)]


def _standardize(samples: list[dict]) -> tuple[list[float], list[float]]:
    columns = list(zip(*(sample["features"] for sample in samples)))
    means = [sum(column) / len(column) for column in columns]
    scales = []
    for column, mean in zip(columns, means):
        variance = sum((value - mean) ** 2 for value in column) / max(1, len(column) - 1)
        scales.append(max(math.sqrt(variance), 1e-8))
    return means, scales


def _vector(features: list[float], means: list[float], scales: list[float]) -> list[float]:
    return [1.0] + [(value - mean) / scale for value, mean, scale in zip(features, means, scales)]


def _fit(samples: list[dict], ridge: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    means, scales = _standardize(samples)
    size = len(FEATURE_NAMES) + 1
    matrix = [[0.0] * size for _ in range(size)]
    vector = [0.0] * size
    total_samples = len(samples)
    for sample_index, sample in enumerate(samples):
        row = _vector(sample["features"], means, scales)
        recency_weight = 0.18 + 0.82 * ((sample_index + 1) / total_samples) ** 2
        for i in range(size):
            vector[i] += recency_weight * row[i] * sample["target"]
            for j in range(size):
                matrix[i][j] += recency_weight * row[i] * row[j]
    for i in range(1, size):
        matrix[i][i] += ridge
    return _solve(matrix, vector), means, scales


def _predict(weights: list[float], row: list[float]) -> float:
    return sum(weight * value for weight, value in zip(weights, row))


def _trend_return(features: list[float], horizon: int, profile: str) -> float:
    one_bar = features[0]
    acceleration = features[3]
    trend = features[8]
    mean_gap = features[14]
    if profile == "stablecoin":
        return -0.72 * mean_gap + 0.12 * one_bar
    velocity = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])["velocity"]
    continuation = (velocity * one_bar + (1 - velocity) * trend) * math.sqrt(max(1, horizon))
    mean_reversion = -0.10 * mean_gap if profile in {"etf", "fund", "metal"} else 0.0
    return continuation + 0.16 * acceleration * min(horizon, 3) + mean_reversion


def _analogue_return(reference: list[dict], features: list[float], means: list[float], scales: list[float]) -> float | None:
    if len(reference) < 12:
        return None
    reference = reference[-ANALOGUE_POOL:]
    target = _vector(features, means, scales)[1:]
    ranked = []
    total = len(reference)
    for index, sample in enumerate(reference):
        candidate = _vector(sample["features"], means, scales)[1:]
        distance = math.sqrt(sum((left - right) ** 2 for left, right in zip(target, candidate)) / len(target))
        recency = 0.65 + 0.35 * (index + 1) / total
        ranked.append((distance, recency, float(sample["target"])))
    nearest = sorted(ranked, key=lambda item: item[0])[:min(9, max(4, int(math.sqrt(total))))]
    weights = [recency / max(0.12, distance) for distance, recency, _ in nearest]
    denominator = sum(weights)
    return sum(weight * item[2] for weight, item in zip(weights, nearest)) / denominator if denominator else None


def _component_weights(errors: dict[str, float | None], profile: str = "market") -> dict[str, float]:
    defaults = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])["weights"]
    if not errors or all(value is None for value in errors.values()):
        return defaults
    raw = {name: 1 / max(0.0015, float(errors.get(name) or 0.02)) for name in defaults}
    total = sum(raw.values())
    normalized = {name: value / total for name, value in raw.items()}
    clipped = {name: min(0.68, max(0.12, normalized[name])) for name in defaults}
    clipped_total = sum(clipped.values())
    return {name: value / clipped_total for name, value in clipped.items()}


def _ensemble_return(ridge: float, analogue: float | None, trend: float, weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    components = {"ridge": ridge, "analogue": ridge if analogue is None else analogue, "trend": trend}
    return sum(weights[name] * value for name, value in components.items()), components


def _return_shrinkage(predicted: list[float], actual: list[float], profile: str) -> float:
    if len(predicted) < 6 or len(predicted) != len(actual):
        return MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])["initial_shrink"]
    denominator = sum(value * value for value in predicted)
    if denominator < 1e-12:
        return 0.05
    slope = sum(estimate * realized for estimate, realized in zip(predicted, actual)) / denominator
    return max(0.04, min(1.05, slope))


def _regime_guard(predicted: float, features: list[float], horizon: int, profile: str) -> tuple[float, bool]:
    if profile == "stablecoin":
        return predicted, False
    one_bar = features[0]
    acceleration = features[3]
    ma3_gap = features[4]
    volatility = max(abs(features[9]), 1e-6)
    volume_impulse = features[16]
    fast_signal = 0.52 * one_bar + 0.23 * acceleration + 0.17 * ma3_gap + 0.08 * volume_impulse
    reversal = predicted * fast_signal < 0 and abs(fast_signal) >= 0.42 * volatility
    if not reversal:
        return predicted, False
    guarded = 0.28 * predicted + 0.72 * fast_signal * math.sqrt(max(1, horizon))
    if guarded * fast_signal < 0:
        guarded = 0.0
    return guarded, True


def _live_context_return(live_context: dict | None, features: list[float], horizon: int, profile: str) -> tuple[float, dict]:
    context = live_context or {}
    score = max(-1.0, min(1.0, float(context.get("score") or 0) / 100))
    confidence = max(0.0, min(1.0, float(context.get("confidence") or 0) / 100))
    abnormal = context.get("abnormal") or {}
    metrics = context.get("metrics") or {}
    change = float(metrics.get("change_pct") or 0)
    abnormal_direction = 1.0 if change > 0 else -1.0 if change < 0 else 0.0
    abnormal_signal = abnormal_direction * max(0.0, min(1.0, float(abnormal.get("score") or 0) / 100))
    evidence_signal = 0.78 * score + 0.22 * abnormal_signal
    volatility = max(abs(features[9]), abs(features[0]) * .65, .00035)
    config = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])
    coverage = .25 + .75 * confidence
    estimate = evidence_signal * volatility * math.sqrt(max(1, horizon)) * config["context_scale"] * coverage
    cap = min(.06, max(.002, 2.2 * volatility * math.sqrt(max(1, horizon))))
    estimate = max(-cap, min(cap, estimate))
    return estimate, {
        "score": round(score * 100, 2), "confidence": round(confidence * 100, 2),
        "return_contribution_pct": round((math.exp(estimate) - 1) * 100, 5),
        "method": "price_volume_technical_related_content",
    }


def _free_running_series(predictions: list[dict], horizon: int) -> list[dict]:
    if not predictions:
        return []
    simulated = None
    result = []
    for index, point in enumerate(predictions):
        predicted_return = float(point["predicted_return"])
        if simulated is None:
            simulated = float(point["origin_price"]) * math.exp(predicted_return)
        else:
            simulated *= math.exp(predicted_return / max(1, horizon))
        result.append({
            "time": point["time"], "value": simulated, "origin_time": point["origin_time"],
            "kind": "free_running", "anchored_once": index == 0,
        })
    return result


def _phase_lag(predicted: list[float], actual: list[float], max_lag: int = 3) -> int | None:
    if len(predicted) < 8 or len(actual) != len(predicted):
        return None
    predicted_moves = predicted
    actual_moves = actual

    def correlation(left: list[float], right: list[float]) -> float:
        if len(left) < 5:
            return -2.0
        left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
        numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
        denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
        return numerator / denominator if denominator > 1e-12 else -2.0

    scored = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            left, right = predicted_moves[-lag:], actual_moves[:lag]
        elif lag > 0:
            left, right = predicted_moves[:-lag], actual_moves[lag:]
        else:
            left, right = predicted_moves, actual_moves
        scored.append((correlation(left, right), lag))
    return max(scored)[1]


def adaptive_horizon(market: str, symbol: str, interval: str, asset_type: str | None = None) -> int:
    profile = instrument_profile(market, symbol, asset_type)
    if profile == "stablecoin":
        return 1 if interval == "1d" else 5
    return int(MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])["horizon"])


def _safe_name(market: str, symbol: str, interval: str) -> str:
    safe = "".join(character for character in f"{market}-{symbol}-{interval}" if character.isalnum() or character in "-_")
    return safe or "model"


def _iso_value(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _future_schedule(last_time: str, market: str, interval: str, horizon: int, to_session_close: bool) -> tuple[list[str], str]:
    last = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
    if interval in INTERVAL_MINUTES:
        minutes = INTERVAL_MINUTES[interval]
        if market not in MARKET_SESSIONS:
            return [_iso_value(last + timedelta(minutes=minutes * step)) for step in range(1, horizon + 1)], _horizon_label(interval, horizon)
        zone_name, sessions = MARKET_SESSIONS[market]
        local = last.astimezone(ZoneInfo(zone_name))

        def candidates(day, after_minute=-1):
            if day.weekday() >= 5:
                return []
            points = []
            for start, end in sessions:
                for minute in range(start, end + 1, minutes):
                    if minute <= after_minute:
                        continue
                    point = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=ZoneInfo(zone_name))
                    points.append(_iso_value(point))
            return points

        local_minute = local.hour * 60 + local.minute
        future = candidates(local.date(), local_minute)
        session_label = "至本次收盘"
        if not future:
            next_day = local.date() + timedelta(days=1)
            while next_day.weekday() >= 5:
                next_day += timedelta(days=1)
            future = candidates(next_day)
            session_label = "至下一交易日收盘"
        return (future if to_session_close else future[:horizon]), (session_label if to_session_close else _horizon_label(interval, horizon))

    future = []
    current = last
    while len(future) < horizon:
        current += timedelta(days=1)
        if market == "crypto" or current.weekday() < 5:
            future.append(_iso_value(current))
    return future, _horizon_label(interval, horizon)


def _return_cap(bars: list[dict], horizon: int) -> float:
    closes = [float(bar["close"]) for bar in bars[-40:] if bar.get("close")]
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes)) if closes[index - 1] > 0]
    if len(returns) < 2:
        return 0.04
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1)
    return min(0.20, max(0.012, 3.2 * math.sqrt(variance) * math.sqrt(max(1, horizon))))


def _direct_return(bars: list[dict], horizon: int, profile: str = "market") -> float | None:
    bars = bars[-(PATH_FIT_WINDOW + horizon + 40):]
    samples = build_samples(bars, horizon)
    latest = _features(bars, len(bars) - 1)
    if len(samples) < MIN_SAMPLES or latest is None:
        return None
    config = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])
    ridge = config["ridge_small"] if len(samples) < 40 else config["ridge"]
    weights, means, scales = _fit(samples, ridge=ridge)
    ridge = _predict(weights, _vector(latest, means, scales))
    analogue = _analogue_return(samples, latest, means, scales)
    predicted, _ = _ensemble_return(ridge, analogue, _trend_return(latest, horizon, profile), _component_weights({}, profile))
    predicted, _ = _regime_guard(predicted, latest, horizon, profile)
    cap = _return_cap(bars, horizon)
    return max(-cap, min(cap, predicted))


def _forward_path(bars: list[dict], times: list[str], base_horizon: int, base_return: float, profile: str, shrinkage: float, publication_damping: float, live_context: dict | None, residual_sigma: float) -> list[dict]:
    if not bars or not times:
        return []
    latest_price = float(bars[-1]["close"])
    latest_features = _features(bars, len(bars) - 1)
    result = [{"time": bars[-1]["time"], "value": latest_price, "step": 0, "kind": "origin"}]
    previous_return = 0.0
    for step, target_time in enumerate(times, 1):
        direct = base_return if step == base_horizon else _direct_return(bars, step, profile)
        if direct is None:
            direct = base_return * step / max(1, base_horizon)
        model_return = direct * shrinkage
        context_return, _ = _live_context_return(live_context, latest_features, step, profile) if latest_features else (0.0, {})
        combined = (model_return + context_return) * publication_damping
        smoothed = 0.74 * combined + 0.26 * previous_return
        cap = _return_cap(bars, step)
        smoothed = max(-cap, min(cap, smoothed))
        progress = step / max(1, len(times))
        band_return = min(cap * 1.15, max(abs(latest_features[9]) * math.sqrt(step) if latest_features else 0.0, residual_sigma * math.sqrt(progress)))
        result.append({
            "time": target_time, "value": latest_price * math.exp(smoothed),
            "lower": latest_price * math.exp(smoothed - band_return),
            "upper": latest_price * math.exp(smoothed + band_return),
            "uncertainty_pct": (math.exp(band_return) - 1) * 100,
            "step": step, "kind": "forecast",
        })
        previous_return = smoothed
    return result


def forecast(bars: list[dict], market: str, symbol: str, interval: str = "1d", horizon: int = 3, to_session_close: bool = False, asset_type: str | None = None, live_context: dict | None = None) -> dict:
    samples = build_samples(bars, horizon)
    if len(samples) < MIN_SAMPLES:
        return {"status": "insufficient_data", "required_samples": MIN_SAMPLES, "available_samples": len(samples), "series": {"predicted": [], "actual": []}}
    split = min(len(samples) - 4, max(10, int(len(samples) * 0.72)))
    train, validation = samples[:split], samples[split:]
    model_dir = DATA_DIR / "models"
    model_dir.mkdir(exist_ok=True)
    model_path = model_dir / f"{_safe_name(market, symbol, interval)}.json"
    state = None
    if model_path.is_file():
        try:
            state = json.loads(model_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = None
    profile = instrument_profile(market, symbol, asset_type)
    config = MODEL_PROFILES.get(profile, MODEL_PROFILES["market"])
    valid_state = bool(
        state
        and state.get("version") == STATE_VERSION
        and state.get("feature_names") == FEATURE_NAMES
        and state.get("horizon_bars") == horizon
        and state.get("profile") == profile
    )
    weights, means, scales = _fit(train, ridge=config["ridge_small"] if len(train) < 40 else config["ridge"])
    if not valid_state:
        state = {
            "version": STATE_VERSION, "feature_names": FEATURE_NAMES, "horizon_bars": horizon, "profile": profile,
            "base_samples": len(train),
            "weights": weights, "means": means, "scales": scales,
            "updates": 0, "last_matured_at": None, "error_ema": None, "direction_ema": None,
            "component_error_ema": {"ridge": None, "analogue": None, "trend": None},
        }
    else:
        state["base_samples"] = len(train)
        state["weights"], state["means"], state["scales"] = weights, means, scales

    predictions = []
    actual = []
    evaluation_errors = []
    evaluation_directions = []
    weights = [float(value) for value in weights]
    means = [float(value) for value in means]
    scales = [float(value) for value in scales]
    last_matured = state.get("last_matured_at")
    component_errors = {"ridge": None, "analogue": None, "trend": None}
    known_samples = train[:]
    naive_errors = []
    predicted_returns = []
    actual_returns = []
    raw_returns = []
    regime_guard_count = 0
    for validation_index, sample in enumerate(validation):
        row = _vector(sample["features"], means, scales)
        ridge_return = _predict(weights, row)
        analogue_return = _analogue_return(known_samples, sample["features"], means, scales)
        trend_return = _trend_return(sample["features"], horizon, profile)
        blend = _component_weights(component_errors, profile)
        raw_return, components = _ensemble_return(ridge_return, analogue_return, trend_return, blend)
        raw_return, regime_guarded = _regime_guard(raw_return, sample["features"], horizon, profile)
        regime_guard_count += int(regime_guarded)
        shrinkage = _return_shrinkage(raw_returns, actual_returns, profile)
        predicted_return = raw_return * shrinkage
        predicted_return = max(-0.35, min(0.35, predicted_return))
        predicted_price = sample["origin_price"] * math.exp(predicted_return)
        error_pct = abs(predicted_price / sample["target_price"] - 1) * 100
        direction_ok = (predicted_return >= 0) == (sample["target"] >= 0)
        evaluation_errors.append(error_pct)
        naive_errors.append(abs(sample["origin_price"] / sample["target_price"] - 1) * 100)
        evaluation_directions.append(1.0 if direction_ok else 0.0)
        predicted_returns.append(predicted_return)
        actual_returns.append(sample["target"])
        raw_returns.append(raw_return)
        predictions.append({"time": sample["target_time"], "value": predicted_price, "origin_time": sample["origin_time"], "origin_price": sample["origin_price"], "predicted_return": predicted_return})
        actual.append({"time": sample["target_time"], "value": sample["target_price"]})
        rate = 0.025 / math.sqrt(validation_index + 1)
        residual = predicted_return - sample["target"]
        for index in range(len(weights)):
            penalty = 0.001 * weights[index] if index else 0.0
            weights[index] -= rate * (residual * row[index] + penalty)
        alpha = 0.12
        for name, value in components.items():
            component_error = abs(value - sample["target"])
            previous = component_errors.get(name)
            component_errors[name] = component_error if previous is None else (1 - alpha) * previous + alpha * component_error
        if not last_matured or sample["target_time"] > last_matured:
            state["error_ema"] = error_pct if state["error_ema"] is None else (1 - alpha) * state["error_ema"] + alpha * error_pct
            direction_value = 1.0 if direction_ok else 0.0
            state["direction_ema"] = direction_value if state["direction_ema"] is None else (1 - alpha) * state["direction_ema"] + alpha * direction_value
            state["updates"] += 1
            state["last_matured_at"] = sample["target_time"]
        known_samples.append(sample)

    state["weights"] = weights
    state["component_error_ema"] = component_errors
    model_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    mae = sum(evaluation_errors) / len(evaluation_errors)
    directional = sum(evaluation_directions) / len(evaluation_directions)
    naive_mae = sum(naive_errors) / len(naive_errors)
    skill_vs_naive = (naive_mae - mae) / naive_mae * 100 if naive_mae > 1e-9 else 0.0
    phase_lag = _phase_lag(predicted_returns, actual_returns)
    validation_passed = skill_vs_naive > 0 and directional >= 0.5
    publishable = validation_passed and (phase_lag is None or phase_lag >= 0)
    publication_damping = 1.0 if publishable else 0.35
    realized_error = state["error_ema"] if state["error_ema"] is not None else mae
    realized_direction = state["direction_ema"] if state["direction_ema"] is not None else directional
    sample_factor = min(1.0, math.log1p(len(validation) + state["updates"]) / math.log(160))
    quality = max(0.0, min(1.0, 0.62 * realized_direction + 0.38 * max(0.0, 1 - realized_error / 8)))
    confidence = round(min(85.0, 100 * sample_factor * quality), 2)
    # Keep holdout calibration quality distinct from whether a live forecast may
    # be published. Lagging forecasts can score well historically, but are still
    # damped and withheld from alerts below.
    capped_by_validation = False
    if not validation_passed:
        capped_by_validation = confidence > 34.0
        confidence = min(confidence, 34.0)
    grade = "high" if confidence >= 70 else "medium" if confidence >= 48 else "low"
    latest_features = _features(bars, len(bars) - 1)
    if latest_features:
        ridge_return = _predict(weights, _vector(latest_features, means, scales))
        analogue_return = _analogue_return(samples, latest_features, means, scales)
        raw_next_return, next_components = _ensemble_return(ridge_return, analogue_return, _trend_return(latest_features, horizon, profile), _component_weights(component_errors, profile))
        raw_next_return, latest_regime_guarded = _regime_guard(raw_next_return, latest_features, horizon, profile)
        final_shrinkage = _return_shrinkage(raw_returns, actual_returns, profile)
        context_return, live_context_meta = _live_context_return(live_context, latest_features, horizon, profile)
        next_return = (raw_next_return * final_shrinkage + context_return) * publication_damping
        next_components["live_context"] = context_return
    else:
        next_return, next_components, final_shrinkage, latest_regime_guarded, live_context_meta = 0.0, {"ridge": 0.0, "analogue": 0.0, "trend": 0.0, "live_context": 0.0}, 0.05, False, {}
    base_cap = _return_cap(bars, horizon)
    next_return = max(-base_cap, min(base_cap, next_return))
    latest_price = float(bars[-1]["close"])
    future_times, display_horizon_label = _future_schedule(bars[-1]["time"], market, interval, horizon, to_session_close)
    residuals = [predicted - realized for predicted, realized in zip(predicted_returns, actual_returns)]
    residual_mean = sum(residuals) / len(residuals) if residuals else 0.0
    residual_sigma = math.sqrt(sum((value - residual_mean) ** 2 for value in residuals) / max(1, len(residuals) - 1)) if residuals else 0.0
    forward_series = _forward_path(bars, future_times, horizon, raw_next_return if latest_features else 0.0, profile, final_shrinkage, publication_damping, live_context, residual_sigma)
    terminal = forward_series[-1] if forward_series else {"time": bars[-1]["time"], "value": latest_price, "step": 0}
    terminal_return_pct = (float(terminal["value"]) / latest_price - 1) * 100
    return {
        "status": "ready",
        "method": "adaptive_market_ensemble_sentiment_path_v6",
        "horizon_bars": len(future_times) if to_session_close else horizon,
        "horizon_label": display_horizon_label,
        "calibration_horizon_bars": horizon,
        "calibration_horizon_label": _horizon_label(interval, horizon),
        "training": {"initial_samples": len(train), "base_samples": state.get("base_samples", len(train)), "evaluation_samples": len(validation), "online_updates": state["updates"], "last_matured_at": state.get("last_matured_at"), "refresh_trigger": "each newly observed bar"},
        "evaluation": {"mean_absolute_error_pct": mae, "directional_accuracy": directional * 100, "no_change_error_pct": naive_mae, "skill_vs_no_change_pct": skill_vs_naive, "phase_lag_bars": phase_lag, "validation_passed": validation_passed, "samples": len(validation)},
        "confidence": {"score": confidence, "grade": grade, "kind": "historical_calibration_quality", "not_probability": True, "capped_by_validation": capped_by_validation, "basis": ["chronological holdout error", "directional accuracy", "matured sample count"]},
        "series": {"predicted": _free_running_series(predictions[-60:], horizon), "one_shot_predicted": predictions[-60:], "actual": actual[-60:]},
        "forward_series": forward_series,
        "next_forecast": {"origin_time": bars[-1]["time"], "origin_price": latest_price, "target_time": terminal["time"], "predicted_price": terminal["value"], "predicted_return_pct": terminal_return_pct, "lower_price": terminal.get("lower"), "upper_price": terminal.get("upper"), "horizon_bars": terminal["step"], "horizon_label": display_horizon_label, "publishable": publishable, "publication_damping": publication_damping, "components": next_components},
        "ensemble": {"profile": profile, "state_scope": f"{market}:{symbol}:{interval}", "weights": _component_weights(component_errors, profile), "return_shrinkage": final_shrinkage, "latest_regime_guarded": latest_regime_guarded, "historical_regime_guards": regime_guard_count, "live_context": live_context_meta, "components": ["regularized trend model", "similar historical regimes", "short-horizon velocity", "live market sentiment"]},
        "path": {"scope": "session_close" if to_session_close else "fixed_horizon", "points": len(forward_series), "starts_at": bars[-1]["time"], "ends_at": terminal["time"], "updates_on_new_bar": True},
        "disclaimer": "Statistical estimate from historical bars; not a probability, guarantee, or trading instruction.",
    }
