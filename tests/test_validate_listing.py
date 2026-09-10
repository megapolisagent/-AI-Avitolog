"""Тест avito-listing-packaging/tools/validate_listing.py — финальный чек-лист карточки
перед публикацией, переведён из прозы (SKILL.md, «Финальный валидатор») в код 2026-09-10
по находке независимого аудита."""
import unittest

from _loader import load_module

validate = load_module(
    ".claude/skills/avito-listing-packaging/tools/validate_listing.py", "validate_listing"
)


class TestValidateListing(unittest.TestCase):
    def _base(self, **overrides):
        data = {
            "price_field_value": 22_500_000,
            "full_price": 22_500_000,
            "listing_text": "2-комн. евро, 46,2 м², ипотека от 1 ₽/мес по программе застройщика.",
            "floor_input": 12,
            "floor_in_text": 12,
            "confirmed_percent_facts": [20, 6],
        }
        data.update(overrides)
        return data

    def test_clean_listing_passes_everything(self):
        result = validate.validate_listing(self._base())
        self.assertTrue(result["passed"])
        self.assertTrue(all(c["status"] == "pass" for c in result["checks"]))

    def test_price_field_is_down_payment_not_full_price(self):
        result = validate.validate_listing(self._base(price_field_value=4_500_000))
        self.assertFalse(result["passed"])
        fail = next(c for c in result["checks"] if c["rule"] == "price_field_is_full_price")
        self.assertEqual(fail["status"], "fail")

    def test_phone_number_in_text_fails(self):
        result = validate.validate_listing(
            self._base(listing_text="Звоните +7 (999) 123-45-67 для показа.")
        )
        self.assertFalse(result["passed"])
        fail = next(c for c in result["checks"] if c["rule"] == "no_phone_or_external_links")
        self.assertEqual(fail["status"], "fail")

    def test_external_link_in_text_fails(self):
        result = validate.validate_listing(
            self._base(listing_text="Подробности в нашей группе vk.com/example")
        )
        self.assertFalse(result["passed"])
        fail = next(c for c in result["checks"] if c["rule"] == "no_phone_or_external_links")
        self.assertEqual(fail["status"], "fail")

    def test_invented_floor_without_input_data_fails(self):
        result = validate.validate_listing(
            self._base(floor_input=None, floor_in_text=9)
        )
        self.assertFalse(result["passed"])
        fail = next(c for c in result["checks"] if c["rule"] == "floor_matches_input")
        self.assertEqual(fail["status"], "fail")

    def test_floor_mismatch_between_input_and_text_fails(self):
        result = validate.validate_listing(self._base(floor_input=12, floor_in_text=5))
        self.assertFalse(result["passed"])
        fail = next(c for c in result["checks"] if c["rule"] == "floor_matches_input")
        self.assertEqual(fail["status"], "fail")

    def test_no_floor_data_and_no_floor_claim_passes(self):
        result = validate.validate_listing(self._base(floor_input=None, floor_in_text=None))
        check = next(c for c in result["checks"] if c["rule"] == "floor_matches_input")
        self.assertEqual(check["status"], "pass")

    def test_unbacked_percent_claim_flagged_as_needs_review_not_hard_fail(self):
        result = validate.validate_listing(
            self._base(listing_text="Скидка 50% только сегодня!", confirmed_percent_facts=[20, 6])
        )
        check = next(c for c in result["checks"] if c["rule"] == "percent_claims_backed_by_input")
        self.assertEqual(check["status"], "needs_review")
        # needs_review не должен ронять passed — это ручная проверка, не жёсткий провал.
        self.assertTrue(result["passed"])

    def test_backed_percent_claim_passes(self):
        result = validate.validate_listing(
            self._base(listing_text="Первоначальный взнос 20%, ставка 6% годовых.")
        )
        check = next(c for c in result["checks"] if c["rule"] == "percent_claims_backed_by_input")
        self.assertEqual(check["status"], "pass")

    def test_missing_required_field_raises(self):
        with self.assertRaises(validate.InputError):
            validate.validate_listing({"price_field_value": 100})


if __name__ == "__main__":
    unittest.main()
