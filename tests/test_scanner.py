import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.monitoring.scanner import scan


def payload(change=3.2, forecast_return=1.1, technical=70, volume=2.1):
    return {
        "asset": {"market": "us", "symbol": "TEST", "name": "Test Asset"},
        "quote": {"price": 103.2, "change_pct": change, "source": "Test Feed", "feed": "verified", "as_of": "2026-09-11T10:00:00Z"},
        "technical": {"score": technical, "stance": "bullish", "relative_volume": volume},
        "market_sentiment": {"score": 40 if change >= 0 else -40, "confidence": 80},
        "ml_forecast": {"status": "ready", "method": "v6", "ensemble": {"profile": "us_equity"}, "horizon_label": "至本次收盘", "confidence": {"score": 72}, "evaluation": {"validation_passed": True, "phase_lag_bars": 0}, "next_forecast": {"predicted_return_pct": forecast_return, "predicted_price": 104.3, "target_time": "2026-09-11T20:00:00Z", "publishable": True}},
    }


class ScannerTests(unittest.TestCase):
    def test_detects_potential_and_anomaly_with_stable_ids(self):
        thresholds = {"forecast_pct": 0.7, "price_change_pct": 2.0, "volume_ratio": 1.8}
        first = scan(payload(), thresholds)
        second = scan(payload(), thresholds)
        self.assertEqual({item["category"] for item in first["matches"]}, {"potential", "anomaly"})
        self.assertEqual([item["id"] for item in first["alerts"]], [item["id"] for item in second["alerts"]])
        self.assertEqual(first["source"]["name"], "Test Feed")

    def test_detects_downside_risk(self):
        result = scan(payload(change=-3.0, forecast_return=-1.2, technical=-72), {"forecast_pct": 0.7, "price_change_pct": 2.0, "volume_ratio": 1.8})
        self.assertIn("risk", {item["category"] for item in result["matches"]})
        self.assertEqual(result["status"]["category"], "risk")

    def test_lagging_or_unvalidated_forecast_cannot_trigger_alert(self):
        item = payload(change=0.1, forecast_return=3.0, technical=0, volume=1.0)
        item["ml_forecast"]["evaluation"] = {"validation_passed": True, "phase_lag_bars": -1}
        result = scan(item, {"forecast_pct": 0.7, "price_change_pct": 2.0, "volume_ratio": 1.8})
        self.assertFalse(result["alerts"])

    def test_non_publishable_forecast_cannot_trigger_alert(self):
        item = payload(change=0.1, forecast_return=3.0, technical=0, volume=1.0)
        item["ml_forecast"]["next_forecast"]["publishable"] = False
        result = scan(item, {"forecast_pct": 0.7, "price_change_pct": 2.0, "volume_ratio": 1.8})
        self.assertFalse(result["alerts"])

    def test_bullish_forecast_cannot_override_weak_technical_signal(self):
        item = payload(change=2.2, forecast_return=3.0, technical=-50, volume=1.0)
        item["market_sentiment"]["score"] = 35
        result = scan(item, {"forecast_pct": 0.7, "price_change_pct": 4.0, "volume_ratio": 1.8})
        self.assertNotIn("potential", {entry["category"] for entry in result["matches"]})

    def test_public_risk_evidence_triggers_sourced_alert_without_price_move(self):
        item = payload(change=0.1, forecast_return=0.1, technical=0, volume=1.0)
        item["market_sentiment"]["score"] = 0
        item["company_risk"] = {
            "status": "ready", "priority_score": 82, "detected_count": 1,
            "detected": [{"key": "investigation", "label": "立案调查", "severity": "high", "evidence": [{"title": "收到立案告知书", "source": "交易所公告", "url": "https://example.test/a"}]}],
            "sources": [{"name": "交易所公告"}], "as_of": "2026-09-12T03:00:00Z",
            "disclaimer": "未命中不等于不存在",
        }
        result = scan(item, {"forecast_pct": 0.7, "price_change_pct": 2.0, "volume_ratio": 1.8})
        self.assertEqual({entry["category"] for entry in result["matches"]}, {"risk"})
        self.assertEqual(result["alerts"][0]["source"], "交易所公告")
        self.assertIn("立案调查", result["alerts"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
