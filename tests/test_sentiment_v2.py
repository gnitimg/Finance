import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cache import Cache
import scripts.model_registry as model_registry
from scripts.news import llm_sentiment
from scripts.news.sentiment import analyze


class LexiconV2Tests(unittest.TestCase):
    def test_negation_flips_polarity(self):
        positive = analyze([{"title": "公司业绩大幅增长", "source": "x"}], entity=None)
        negated = analyze([{"title": "公司业绩并未增长", "source": "x"}], entity=None)
        self.assertGreater(positive["score"], 0.3)
        self.assertLess(negated["score"], 0)

    def test_degree_amplifies_and_dampens(self):
        strong = analyze([{"title": "公司利润大幅增长", "source": "x"}])
        modest = analyze([{"title": "公司利润小幅增长", "source": "x"}])
        self.assertGreater(strong["evidence"][0]["score"], modest["evidence"][0]["score"] * 1.5)

    def test_entity_miss_downweights(self):
        hit = analyze([{"title": "英伟达盈利大增", "source": "x"}], entity="英伟达")
        miss = analyze([{"title": "某银行盈利大增", "source": "x"}], entity="英伟达")
        self.assertGreater(hit["evidence"][0]["score"], miss["evidence"][0]["score"] * 2)
        self.assertTrue(miss["evidence"][0].get("entity_miss"))


class LLMSentimentTests(unittest.TestCase):
    def setUp(self):
        self._registry_dir = tempfile.TemporaryDirectory()
        model_registry.CONFIG_PATH = Path(self._registry_dir.name) / 'model_endpoints.json'
        self._previous_cache = llm_sentiment.CACHE
        self._directory = tempfile.TemporaryDirectory()
        llm_sentiment.CACHE = Cache(Path(self._directory.name) / "cache.sqlite3")

    def tearDown(self):
        model_registry.CONFIG_PATH = Path.home() / '.hermes' / '.env'  # restored below
        llm_sentiment.CACHE = self._previous_cache
        self._directory.cleanup()

    def test_disabled_by_default(self):
        self.assertFalse(llm_sentiment.enabled())

    @patch.dict("os.environ", {"FINANCE_SENTIMENT_LLM_ENABLED": "true", "OPENCODE_ZEN_API_KEY": "test-key"}, clear=False)
    @patch("scripts.news.llm_sentiment.urllib.request.urlopen")
    def test_scores_are_cached_and_blended(self, urlopen):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({"choices": [{"message": {"content": '[{"i": 0, "s": -0.8}, {"i": 1, "s": 0.6}]'}}]}).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response
        items = [
            {"title": "公司盈利大增", "url": "https://x/1"},
            {"title": "公司亏损加重", "url": "https://x/2"},
        ]
        scores = llm_sentiment.score_items(items)
        self.assertAlmostEqual(scores["https://x/1"], -0.8)
        second = llm_sentiment.score_items(items)
        self.assertEqual(urlopen.call_count, 1)  # second call served from cache
        self.assertAlmostEqual(second["https://x/2"], 0.6)
        sentiment = analyze(items)
        blended = llm_sentiment.apply(sentiment, items, scores)
        self.assertEqual(blended["method"], "lexicon_v2+llm")
        self.assertEqual(blended["llm_scored"], 2)

    @patch.dict("os.environ", {"FINANCE_SENTIMENT_LLM_ENABLED": "true", "OPENCODE_ZEN_API_KEY": "test-key"}, clear=False)
    @patch("scripts.news.llm_sentiment.CACHE.get", return_value=({"until": 9_999_999_999}, {}))
    def test_cooldown_blocks_calls(self, _cache_get):
        with self.assertRaises(Exception):
            llm_sentiment.score_items([{"title": "a", "url": "https://x/1"}])


if __name__ == "__main__":
    unittest.main()


class StructuredRiskTests(unittest.TestCase):
    def test_structured_findings_upgrade_source_limited_categories(self):
        from scripts.analyzers.company_risk import analyze as risk_analyze
        structured = {
            "sources": ["东方财富数据中心"],
            "findings": {
                "equity_pledge": {"detected": False, "detail": "质押比例 25.03%（中登 2026-09-11）"},
                "lockup_expiry": {"detected": False, "detail": "下次解禁 2028-09-26，占 18.87%"},
            },
        }
        result = risk_analyze([], market="cn", entity="600619", structured=structured)
        by_key = {c["key"]: c for c in result["categories"]}
        self.assertEqual(by_key["equity_pledge"]["status"], "no_evidence")
        self.assertEqual(by_key["lockup_expiry"]["status"], "no_evidence")
        self.assertEqual(by_key["goodwill"]["status"], "source_limited")  # not yet covered
        self.assertEqual(result["method"], "deterministic_public_evidence_screen_v2")

    def test_structured_flag_becomes_detected_with_evidence(self):
        from scripts.analyzers.company_risk import analyze as risk_analyze
        structured = {
            "findings": {"equity_pledge": {"detected": True, "detail": "质押比例 62.00%（中登 2026-09-11）"}},
        }
        result = risk_analyze([], market="cn", entity="600000", structured=structured)
        by_key = {c["key"]: c for c in result["categories"]}
        self.assertEqual(by_key["equity_pledge"]["status"], "detected")
        self.assertEqual(by_key["equity_pledge"]["evidence"][0]["type"], "structured")
        self.assertGreater(result["priority_score"], 50)

    def test_risk_factor_pulls_sentiment_negative(self):
        from scripts.analyzers.market_sentiment import analyze as sentiment_analyze
        bars = [{"close": 10 + i * 0.05, "volume": 1000} for i in range(3)]
        quote = {"change_pct": 0.2}
        clean = sentiment_analyze(bars, quote, {"score": 0}, None, risk=None)
        risky = sentiment_analyze(bars, quote, {"score": 0}, None, risk={"detected_count": 2, "priority_score": 70})
        self.assertLess(risky["score"], clean["score"])
        risk_factor = next(f for f in risky["factors"] if f["key"] == "structured_risk")
        self.assertEqual(risk_factor["direction"], "negative")

    def test_risk_factor_hidden_without_structured_data(self):
        from scripts.analyzers.market_sentiment import analyze as sentiment_analyze
        bars = [{"close": 10 + i * 0.05, "volume": 1000} for i in range(3)]
        result = sentiment_analyze(bars, {"change_pct": 0.2}, {"score": 0}, None, risk=None)
        self.assertNotIn("structured_risk", [f["key"] for f in result["factors"]])


class EventAssessmentTests(unittest.TestCase):
    def setUp(self):
        self._registry_dir = tempfile.TemporaryDirectory()
        self._original_registry_path = model_registry.CONFIG_PATH
        model_registry.CONFIG_PATH = Path(self._registry_dir.name) / "model_endpoints.json"
        self._original_cache = llm_sentiment.CACHE
        llm_sentiment.CACHE = Cache(Path(self._registry_dir.name) / "cache.sqlite3")

    def tearDown(self):
        model_registry.CONFIG_PATH = self._original_registry_path
        llm_sentiment.CACHE = self._original_cache
        self._registry_dir.cleanup()

    @patch.dict("os.environ", {"FINANCE_SENTIMENT_LLM_ENABLED": "true", "OPENCODE_ZEN_API_KEY": "test-key"}, clear=False)
    @patch("scripts.news.llm_sentiment.urllib.request.urlopen")
    def test_parses_direction_confidence(self, urlopen):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({"choices": [{"message": {"content": '{"direction": -0.7, "confidence": 0.8, "events": ["净利润下滑"]}'}}]}).encode()
        response.__enter__.return_value = response
        urlopen.return_value = response
        items = [{"title": "净利润下降48%", "url": "https://x/1"}]
        result = llm_sentiment.event_assessment(items, entity="测试公司")
        self.assertAlmostEqual(result["direction"], -0.7)
        self.assertAlmostEqual(result["confidence"], 0.8)

    @patch.dict("os.environ", {}, clear=False)
    def test_no_chat_model_returns_none(self):
        self.assertIsNone(llm_sentiment.event_assessment([{"title": "x"}], entity="e"))
