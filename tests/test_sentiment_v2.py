import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cache import Cache
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
        self._previous_cache = llm_sentiment.CACHE
        self._directory = tempfile.TemporaryDirectory()
        llm_sentiment.CACHE = Cache(Path(self._directory.name) / "cache.sqlite3")

    def tearDown(self):
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
