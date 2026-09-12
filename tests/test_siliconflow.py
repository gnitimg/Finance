import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cache import Cache
from scripts.news import siliconflow


def _response(payload):
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    return response


class SiliconFlowTests(unittest.TestCase):
    def setUp(self):
        self._previous_cache = siliconflow.CACHE
        self._directory = tempfile.TemporaryDirectory()
        siliconflow.CACHE = Cache(Path(self._directory.name) / "cache.sqlite3")

    def tearDown(self):
        siliconflow.CACHE = self._previous_cache
        self._directory.cleanup()

    def test_disabled_without_key(self):
        with patch.dict("os.environ", {"SILICONFLOW_API_KEY": ""}, clear=False):
            self.assertFalse(siliconflow.enabled())

    @patch.dict("os.environ", {"SILICONFLOW_API_KEY": "test-key", "FINANCE_SENTIMENT_SILICONFLOW_ENABLED": "true"}, clear=False)
    @patch("scripts.news.siliconflow.urllib.request.urlopen")
    def test_contrastive_scores_and_caching(self, urlopen):
        urlopen.side_effect = [
            _response({"results": [{"index": 0, "relevance_score": 0.9}]}),  # positive query
            _response({"results": [{"index": 0, "relevance_score": 0.3}]}),  # negative query
            _response({"results": [{"index": 0, "relevance_score": 0.2}]}),  # repeat: cached, none
            _response({"results": [{"index": 0, "relevance_score": 0.1}]}),
        ]
        items = [{"title": "盈利大增", "url": "https://x/1"}]
        scores = siliconflow.score_items(items)
        self.assertAlmostEqual(scores["https://x/1"], 0.6, places=6)
        siliconflow.score_items(items)  # served from cache
        self.assertEqual(urlopen.call_count, 2)

    @patch.dict("os.environ", {"SILICONFLOW_API_KEY": "test-key", "FINANCE_SENTIMENT_SILICONFLOW_ENABLED": "true"}, clear=False)
    @patch("scripts.news.siliconflow.CACHE.get", return_value=({"until": 9_999_999_999}, {}))
    def test_cooldown_blocks_calls(self, _cache_get):
        with self.assertRaises(Exception):
            siliconflow.score_items([{"title": "x", "url": "https://x/1"}])


if __name__ == "__main__":
    unittest.main()
