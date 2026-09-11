import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.news.sentiment import analyze
from scripts.analyzers.market_sentiment import analyze as market_sentiment


class SentimentTests(unittest.TestCase):
    def test_confidence_is_evidence_quality(self):
        result = analyze([{"title": "Profit growth beats record", "source": "A"}, {"title": "公司盈利增长", "source": "B"}])
        self.assertGreater(result["score"], 0)
        self.assertEqual(result["confidence_kind"], "evidence_quality_not_price_probability")
        self.assertLessEqual(result["confidence"], 100)

    def test_market_sentiment_combines_anomaly_volume_and_related_content(self):
        bars = []
        for index in range(30):
            close = 100 + (index % 3) * 0.15
            bars.append({"close": close, "high": close + 1, "low": close - 1, "volume": 1000})
        quote = {"price": 108, "change_pct": 8, "high": 112, "low": 99, "volume": 4200, "source": "Test feed"}
        news = {"score": 0.6, "label": "positive", "evidence_count": 3, "article_count": 5, "source_count": 2, "evidence": [{"title": "related"}]}
        result = market_sentiment(bars, quote, {"score": 50}, news)
        self.assertEqual(result["label"], "bullish")
        self.assertTrue(result["abnormal"]["detected"])
        self.assertEqual(len(result["factors"]), 4)
        self.assertEqual(result["confidence_kind"], "market_evidence_coverage_not_return_probability")


if __name__ == "__main__":
    unittest.main()
