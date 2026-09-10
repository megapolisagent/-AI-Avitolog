"""Тест scripts/market_snapshot_analyzer.py — дедупликация, эталонный порог, устаревание.
Использует реальные пороги из config.json (не мокается — детерминированный расчёт
должен считаться так же, как в бою)."""
import unittest

from _loader import load_module

analyzer = load_module("scripts/market_snapshot_analyzer.py", "market_snapshot_analyzer")


def lot(**kwargs):
    base = {"title": "т", "price": 20_000_000, "area": 60, "rooms": 2,
            "seller_type": "owner", "url": "http://x"}
    base.update(kwargs)
    return base


class TestDuplicates(unittest.TestCase):
    def test_same_flat_different_sellers_is_duplicate(self):
        lots = [
            lot(price=20_000_000, area=60.0, seller_type="agent"),
            lot(price=20_050_000, area=60.5, seller_type="owner"),  # в пределах допуска
        ]
        dup = analyzer.detect_duplicates(lots)
        self.assertEqual(dup, [None, 0])

    def test_developer_never_marked_duplicate(self):
        lots = [
            lot(price=20_000_000, area=60.0, seller_type="developer"),
            lot(price=20_000_000, area=60.0, seller_type="developer"),
        ]
        dup = analyzer.detect_duplicates(lots)
        self.assertEqual(dup, [None, None])

    def test_different_rooms_not_duplicate(self):
        lots = [
            lot(price=20_000_000, area=60.0, rooms=1, seller_type="agent"),
            lot(price=20_000_000, area=60.0, rooms=2, seller_type="owner"),
        ]
        dup = analyzer.detect_duplicates(lots)
        self.assertEqual(dup, [None, None])


class TestAnalyze(unittest.TestCase):
    def test_below_benchmark_flagged_suspicious(self):
        # Эталон 2-к min=500500 ₽/м² (ASTERUS_BENCHMARK_PPM2) — цена намного ниже 15%-порога.
        cheap = lot(price=10_000_000, area=60, rooms=2, seller_type="owner")
        report = analyzer.analyze([cheap])
        group = report["groups"]["2"]
        self.assertEqual(group["suspicious_underpriced"], 1)

    def test_missing_required_field_is_warned_not_crashed(self):
        broken = {"title": "нет цены/площади"}
        report = analyzer.analyze([broken])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertEqual(report["groups"], {})

    def test_developer_listing_age_ignored_for_staleness(self):
        dev = lot(seller_type="developer", listing_age_days=999)
        report = analyzer.analyze([dev])
        group = report["groups"]["2"]
        self.assertEqual(group["stale_secondary_lots"], 0)

    def test_stale_secondary_listing_flagged(self):
        old = lot(seller_type="owner", listing_age_days=100)
        report = analyzer.analyze([old])
        group = report["groups"]["2"]
        self.assertEqual(group["stale_secondary_lots"], 1)


if __name__ == "__main__":
    unittest.main()
