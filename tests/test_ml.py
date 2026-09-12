import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.monitoring import ml
from scripts.finance import align_model_history, latest_market_session


def synthetic(count=120):
    result = []
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        close = 100 + index * .12 + math.sin(index / 4) * 2
        time = (started + timedelta(minutes=5 * index)).isoformat(timespec="seconds").replace("+00:00", "Z")
        result.append({"time": time, "timestamp": index, "close": close, "open": close - .2, "high": close + .6, "low": close - .6, "volume": 1000 + 120 * math.sin(index / 7)})
    return result


class MLTests(unittest.TestCase):
    def test_intraday_display_keeps_only_latest_exchange_date(self):
        bars = [
            {"time": "2026-09-10T19:55:00Z", "close": 100},
            {"time": "2026-09-11T13:30:00Z", "close": 101},
            {"time": "2026-09-11T19:55:00Z", "close": 102},
        ]
        latest = latest_market_session(bars, "America/New_York")
        self.assertEqual([bar["close"] for bar in latest], [101, 102])

    def test_extended_context_uses_exact_visible_live_edge(self):
        context = [
            {"time": "2026-09-11T15:50:00Z", "close": 100},
            {"time": "2026-09-11T15:55:07Z", "close": 101},
        ]
        visible = [{"time": "2026-09-11T15:55:08Z", "close": 102}]
        aligned = align_model_history(context, visible, "5m")
        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned[-1], visible[-1])

    def test_prediction_and_actual_are_separate_and_feedback_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = ml.DATA_DIR
            ml.DATA_DIR = Path(directory)
            try:
                first = ml.forecast(synthetic(), "us", "TEST", "5m", 3)
                self.assertEqual(first["status"], "ready")
                self.assertTrue(first["series"]["predicted"])
                self.assertEqual(len(first["series"]["predicted"]), len(first["series"]["actual"]))
                self.assertEqual(len(first["series"]["one_shot_predicted"]), len(first["series"]["actual"]))
                self.assertTrue(all(point["kind"] == "free_running" for point in first["series"]["predicted"]))
                self.assertTrue(first["confidence"]["not_probability"])
                self.assertEqual(first["horizon_label"], "15 分钟")
                self.assertEqual(first["next_forecast"]["horizon_label"], "15 分钟")
                self.assertEqual(len(first["forward_series"]), 4)
                self.assertTrue(first["path"]["updates_on_new_bar"])
                self.assertEqual(first["method"], "adaptive_market_ensemble_sentiment_path_v13")
                self.assertEqual(set(first["ensemble"]["weights"]), {"ridge", "analogue", "trend", "reversion"})
                self.assertGreater(first["ensemble"]["return_shrinkage"], 0)
                self.assertIn("phase_lag_bars", first["evaluation"])
                self.assertIn("skill_vs_no_change_pct", first["evaluation"])
                self.assertIsInstance(first["evaluation"]["validation_passed"], bool)
                self.assertIn("publishable", first["next_forecast"])
                self.assertIn(first["next_forecast"]["publication_damping"], {0.35, 1.0})
                self.assertTrue(all("lower" in point and "upper" in point for point in first["forward_series"][1:]))
                self.assertEqual(first["ensemble"]["state_scope"], "us:TEST:5m")
                self.assertGreater(first["training"]["base_samples"], 0)
                self.assertEqual(first["training"]["state_version"], 13)
                updates = first["training"]["online_updates"]
                second = ml.forecast(synthetic(), "us", "TEST", "5m", 3)
                self.assertEqual(second["training"]["online_updates"], updates)
            finally:
                ml.DATA_DIR = previous

    def test_cn_intraday_schedule_skips_lunch_and_reaches_close(self):
        lunch_times, label = ml._future_schedule("2026-09-11T03:30:00Z", "cn", "5m", 3, True)
        afternoon_times, _ = ml._future_schedule("2026-09-11T05:25:00Z", "cn", "5m", 3, True)
        self.assertEqual(label, "至本次收盘")
        self.assertEqual(lunch_times[0], "2026-09-11T05:00:00Z")
        self.assertEqual(lunch_times[-1], "2026-09-11T07:00:00Z")
        self.assertEqual(afternoon_times[-1], "2026-09-11T07:00:00Z")

    def test_market_specific_horizons_are_more_responsive(self):
        self.assertEqual(ml.adaptive_horizon("cn", "601619", "5m"), 1)
        self.assertEqual(ml.adaptive_horizon("hk", "00700", "1d"), 1)
        self.assertEqual(ml.adaptive_horizon("us", "NVDA", "5m"), 3)
        self.assertEqual(ml.adaptive_horizon("crypto", "USDT", "1d"), 1)
        self.assertEqual(ml.adaptive_horizon("crypto", "USDT", "5m"), 5)
        self.assertEqual(ml.adaptive_horizon("etf", "SPY", "5m"), 3)
        self.assertEqual(ml.adaptive_horizon("future", "ES=F", "5m"), 2)
        self.assertEqual(ml.instrument_profile("us", "SPY", "etf"), "etf")

    def test_free_running_path_does_not_reanchor_to_each_actual_origin(self):
        predictions = [
            {"time": "2026-01-01T00:05:00Z", "origin_time": "2026-01-01T00:00:00Z", "origin_price": 100, "predicted_return": 0.01},
            {"time": "2026-01-01T00:10:00Z", "origin_time": "2026-01-01T00:05:00Z", "origin_price": 200, "predicted_return": 0.02},
        ]
        path = ml._free_running_series(predictions, 1)
        self.assertAlmostEqual(path[0]["value"], 100 * math.exp(0.01))
        self.assertAlmostEqual(path[1]["value"], 100 * math.exp(0.03))
        self.assertTrue(path[0]["anchored_once"])
        self.assertFalse(path[1]["anchored_once"])

    def test_recent_downside_reversal_damps_stale_bullish_forecast(self):
        features = [0.0] * len(ml.FEATURE_NAMES)
        features[0] = -0.03
        features[3] = -0.025
        features[4] = -0.012
        features[9] = 0.01
        features[16] = -0.04
        guarded, activated = ml._regime_guard(0.05, features, 3, "market")
        self.assertTrue(activated)
        self.assertGreaterEqual(guarded, 0)
        self.assertLess(guarded, 0.01)

    def test_amplitude_calibration_is_robust_and_never_inverts(self):
        aligned = ml._return_shrinkage([.01] * 20, [.005] * 19 + [.20], "us_equity")
        opposite = ml._return_shrinkage([.01] * 20, [-.005] * 20, "us_equity")
        self.assertGreater(aligned, 0)
        self.assertLess(aligned, 1)
        self.assertEqual(opposite, 0)

    def test_live_sentiment_changes_forward_context_without_llm(self):
        features = [0.0] * len(ml.FEATURE_NAMES)
        features[0] = 0.01
        features[9] = 0.008
        positive, metadata = ml._live_context_return({"score": 65, "confidence": 80, "metrics": {"change_pct": 2}, "abnormal": {"score": 55}}, features, 3, "us_equity")
        negative, _ = ml._live_context_return({"score": -65, "confidence": 80, "metrics": {"change_pct": -2}, "abnormal": {"score": 55}}, features, 3, "us_equity")
        self.assertGreater(positive, 0)
        self.assertLess(negative, 0)
        self.assertEqual(metadata["method"], "price_volume_technical_related_content_flow_orderbook")

    def test_standardization_clips_zero_variance_feature_spikes(self):
        row = ml._vector([10.0, -10.0], [0.0, 0.0], [1e-8, 1e-8])
        self.assertEqual(row, [1.0, ml.MAX_STANDARD_SCORE, -ml.MAX_STANDARD_SCORE])

    def test_session_path_uses_bounded_direct_model_fits(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = ml.DATA_DIR
            ml.DATA_DIR = Path(directory)
            try:
                result = ml.forecast(synthetic(400), "us", "FASTPATH", "5m", 3, to_session_close=True)
                self.assertLessEqual(result["path"]["direct_model_fits"], ml.PATH_MAX_FITS)
                self.assertGreater(len(result["forward_series"]), result["path"]["direct_model_fits"])
                values = {round(point["value"], 8) for point in result["forward_series"]}
                self.assertGreater(len(values), 3)
            finally:
                ml.DATA_DIR = previous

    def test_component_meta_weights_penalize_lagging_high_error_signal(self):
        errors = {"ridge": .003, "analogue": .018, "trend": .024, "reversion": .009}
        directions = {"ridge": .72, "analogue": .48, "trend": .31, "reversion": .57}
        weights = ml._component_weights(errors, "us_equity", directions, baseline_error=.01)
        self.assertGreater(weights["ridge"], weights["trend"])
        self.assertGreater(weights["reversion"], weights["trend"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_model_refits_when_training_context_grows_materially(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = ml.DATA_DIR
            ml.DATA_DIR = Path(directory)
            try:
                first = ml.forecast(synthetic(80), "us", "EXPAND", "1d", 3)
                second = ml.forecast(synthetic(150), "us", "EXPAND", "1d", 3)
                self.assertEqual(second["horizon_label"], "3 个交易日")
                self.assertGreater(second["training"]["base_samples"], first["training"]["base_samples"])
            finally:
                ml.DATA_DIR = previous


if __name__ == "__main__":
    unittest.main()
