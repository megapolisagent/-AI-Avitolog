"""Тест чистого ядра listing-fin-offer/tools/calculate_payment.py — детерминированный
расчёт платежа, не должен быть эмулирован моделью на глаз (SKILL.md, «Принцип»)."""
import unittest

from _loader import load_module

calc = load_module(".claude/skills/listing-fin-offer/tools/calculate_payment.py", "calculate_payment")


class TestMortgage(unittest.TestCase):
    def test_annuity_matches_hand_calc(self):
        result = calc.calculate_payment({
            "price": 22_500_000,
            "down_payment_percent": 20,
            "term_months": 240,
            "annual_rate_percent": 6,
        })
        self.assertEqual(result["product"], "mortgage")
        self.assertEqual(result["down_payment_amount"], 4_500_000.0)
        self.assertEqual(result["principal"], 18_000_000.0)
        # Аннуитет — не точная копия готовой цифры, а диапазон вокруг проверенной вручную
        # оценки (~129 000 ₽/мес при 6% на 20 лет), чтобы тест не стал хрупким к округлению.
        self.assertAlmostEqual(result["monthly_payment"], 128957.59, delta=1.0)
        self.assertGreater(result["overpayment"], 0)

    def test_zero_rate_mortgage_is_plain_division(self):
        result = calc.calculate_payment({
            "price": 12_000_000, "down_payment": 2_000_000,
            "term_months": 20, "annual_rate_percent": 0,
        })
        self.assertEqual(result["monthly_payment"], 500_000.0)
        self.assertEqual(result["overpayment"], 0.0)


class TestInstallment(unittest.TestCase):
    def test_zero_percent_installment_is_plain_division(self):
        result = calc.calculate_payment({
            "price": 22_500_000, "down_payment_percent": 30,
            "term_months": 24, "installment_markup_percent": 0,
        })
        self.assertEqual(result["product"], "installment")
        self.assertEqual(result["monthly_payment"], 656_250.0)
        self.assertEqual(result["overpayment"], 0.0)

    def test_markup_increases_total(self):
        no_markup = calc.calculate_payment({
            "price": 10_000_000, "down_payment": 0,
            "term_months": 12, "installment_markup_percent": 0,
        })
        with_markup = calc.calculate_payment({
            "price": 10_000_000, "down_payment": 0,
            "term_months": 12, "installment_markup_percent": 10,
        })
        self.assertGreater(with_markup["total_paid"], no_markup["total_paid"])
        self.assertEqual(with_markup["overpayment_percent"], 10.0)


class TestInputErrors(unittest.TestCase):
    """Не додумывает на неполном вводе — падает с понятной причиной (эпистемические маркеры)."""

    def test_missing_product_type_raises(self):
        with self.assertRaises(calc.InputError):
            calc.calculate_payment({"price": 10_000_000, "term_months": 12})

    def test_both_product_types_raises(self):
        with self.assertRaises(calc.InputError):
            calc.calculate_payment({
                "price": 10_000_000, "down_payment": 0, "term_months": 12,
                "annual_rate_percent": 5, "installment_markup_percent": 0,
            })

    def test_missing_down_payment_raises(self):
        with self.assertRaises(calc.InputError):
            calc.calculate_payment({"price": 10_000_000, "term_months": 12, "annual_rate_percent": 5})

    def test_down_payment_exceeds_price_raises(self):
        with self.assertRaises(calc.InputError):
            calc.calculate_payment({
                "price": 10_000_000, "down_payment": 10_000_000,
                "term_months": 12, "annual_rate_percent": 5,
            })


if __name__ == "__main__":
    unittest.main()
