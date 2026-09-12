import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyzers.company_risk import RISK_CATEGORIES, analyze


class CompanyRiskTests(unittest.TestCase):
    def test_catalog_covers_requested_risk_dimensions(self):
        self.assertEqual(len(RISK_CATEGORIES), 19)
        self.assertEqual(
            {item["key"] for item in RISK_CATEGORIES},
            {
                "negative_public_opinion", "equity_pledge", "investigation", "violation_penalty",
                "regulatory_inquiry", "negative_rating", "lockup_expiry", "shareholder_reduction",
                "large_order_outflow", "earnings_risk", "audit_opinion", "financial_analysis",
                "st_risk", "goodwill", "deposit_loan_high", "cashflow_interruption",
                "inventory_impairment", "receivables_bad_debt", "financial_distress",
            },
        )

    def test_detects_public_evidence_and_marks_statement_gaps(self):
        items = [{
            "title": "公司收到监管问询函并被立案调查，审计报告无法表示意见，股东拟减持",
            "source": "交易所公告摘要", "published_at": "2026-09-12T01:00:00Z",
            "url": "https://example.test/risk",
        }, {
            "title": "中报净利润为2.37亿元，较去年同期下降48.40%",
            "source": "财报摘要", "published_at": "2026-09-11T02:00:00Z",
            "url": "https://example.test/earnings",
        }]
        result = analyze(items, market="cn", entity="测试公司", news_providers=["eastmoney"])
        detected = {item["key"] for item in result["detected"]}
        self.assertTrue({"regulatory_inquiry", "investigation", "audit_opinion", "shareholder_reduction", "earnings_risk"}.issubset(detected))
        goodwill = next(item for item in result["categories"] if item["key"] == "goodwill")
        self.assertEqual(goodwill["status"], "source_limited")
        self.assertGreaterEqual(result["priority_score"], 82)

    def test_explicitly_resolved_events_are_not_reported_as_hits(self):
        items = [{"title": "公司解除股份质押并澄清未被立案调查", "source": "公告", "url": "https://example.test/clear"}]
        result = analyze(items, market="cn", news_providers=["eastmoney"])
        self.assertNotIn("equity_pledge", {item["key"] for item in result["detected"]})
        self.assertNotIn("investigation", {item["key"] for item in result["detected"]})

    def test_material_fund_outflow_is_a_sourced_proxy(self):
        flow = [
            {"date": f"2026-09-{day:02d}", "main_net": value}
            for day, value in enumerate((100, 120, 80, 110, 90, 105, -900), start=1)
        ]
        result = analyze([], market="cn", flow=flow)
        outflow = next(item for item in result["categories"] if item["key"] == "large_order_outflow")
        self.assertEqual(outflow["status"], "detected")
        self.assertEqual(outflow["evidence"][0]["source"], "东方财富主力资金流")
        self.assertEqual(result["flow"]["latest_main_net"], -900)

    def test_no_feed_is_unavailable_not_safe(self):
        result = analyze([], market="us")
        self.assertEqual(result["status"], "source_unavailable")
        self.assertTrue(all(item["status"] == "unavailable" for item in result["categories"]))
        self.assertIn("不表示风险不存在", result["disclaimer"])


if __name__ == "__main__":
    unittest.main()
