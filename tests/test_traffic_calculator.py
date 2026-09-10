"""Тест traffic-calculator/calculate.py — детерминированный шаг ставки CPA/CPX,
используется субагентом traffic-manager (Модуль 1)."""
import unittest

from _loader import load_module

calc = load_module(".claude/skills/traffic-calculator/tools/calculate.py", "traffic_calculate")


class TestBidRecommendation(unittest.TestCase):
    def test_few_shot_example_from_skill_md(self):
        # Тот же вход, что few-shot в .claude/agents/traffic-manager.md — вердикт должен совпасть.
        result = calc.calculate_bid_recommendation({
            "current_bid": 280, "current_cpa": 420, "target_cpa": 350,
            "spend_period_days": 3, "spend_amount": 8400,
        })
        self.assertEqual(result["action"], "increase_bid")
        self.assertEqual(result["bid_change_percent"], 10)
        self.assertEqual(result["new_bid"], 308.0)

    def test_within_threshold_no_change(self):
        result = calc.calculate_bid_recommendation({
            "current_bid": 300, "current_cpa": 360, "target_cpa": 350,
            "spend_period_days": 5,
        })
        self.assertEqual(result["action"], "no_change")

    def test_short_period_is_low_confidence(self):
        result = calc.calculate_bid_recommendation({
            "current_bid": 300, "current_cpa": 500, "target_cpa": 350,
            "spend_period_days": 1,
        })
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["action"], "no_change")

    def test_big_spend_no_conversions_flags_listing_not_bid(self):
        result = calc.calculate_bid_recommendation({
            "current_bid": 300, "current_cpa": 500, "target_cpa": 350,
            "spend_period_days": 5, "spend_amount": 50_000, "conversions": 0,
        })
        self.assertEqual(result["action"], "review_listing")

    def test_cpa_below_target_recommends_decrease(self):
        result = calc.calculate_bid_recommendation({
            "current_bid": 300, "current_cpa": 200, "target_cpa": 350,
            "spend_period_days": 5,
        })
        self.assertEqual(result["action"], "decrease_bid")
        self.assertLess(result["new_bid"], 300)

    def test_missing_required_field_raises(self):
        with self.assertRaises(calc.InputError):
            calc.calculate_bid_recommendation({"current_bid": 300})


if __name__ == "__main__":
    unittest.main()
