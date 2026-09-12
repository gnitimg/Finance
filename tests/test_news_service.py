import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cache import Cache
from scripts.news import service


class NewsServiceTests(unittest.TestCase):
    def setUp(self):
        self._previous_cache = service.CACHE
        self._directory = tempfile.TemporaryDirectory()
        service.CACHE = Cache(Path(self._directory.name) / "cache.sqlite3")

    def tearDown(self):
        service.CACHE = self._previous_cache
        self._directory.cleanup()

    @patch("scripts.news.siliconflow.score_items")
    @patch("scripts.news.service._yahoo", return_value=[])
    @patch("scripts.news.service._gdelt", return_value=[])
    @patch("scripts.news.service._eastmoney")
    def test_routine_news_is_local_even_when_remote_scorer_exists(self, eastmoney, _gdelt, _yahoo, remote_score):
        eastmoney.return_value = [{
            "title": "测试公司净利润为1亿元，较去年同期下降40%",
            "url": "https://example.test/news", "source": "东方财富", "published_at": "2026-09-12 09:00:00",
        }]
        result = service.get_news("cn", "601619", related_name="测试公司")
        remote_score.assert_not_called()
        self.assertEqual(result["sentiment"]["method"], "lexicon_v2_negation_degree_entity")
        self.assertIn("earnings_risk", {item["key"] for item in result["company_risk"]["detected"]})
        self.assertTrue(result["providers_checked"])


if __name__ == "__main__":
    unittest.main()
