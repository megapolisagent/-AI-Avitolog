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

    def test_secondary_market_lot_skips_developer_group_and_legal_block(self):
        result = validate.validate_listing(self._base())
        group_check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        legal_check = next(c for c in result["checks"] if c["rule"] == "legal_block_present")
        self.assertEqual(group_check["status"], "pass")
        self.assertEqual(legal_check["status"], "pass")
        self.assertTrue(result["passed"])

    def test_developer_group_3_always_fails(self):
        result = validate.validate_listing(self._base(developer_group=3))
        check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        self.assertEqual(check["status"], "fail")
        self.assertFalse(result["passed"])

    def test_developer_group_2_without_acceptance_fails(self):
        result = validate.validate_listing(
            self._base(developer_group=2, developer_acceptance_confirmed=False)
        )
        check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        self.assertEqual(check["status"], "fail")
        self.assertFalse(result["passed"])

    def test_developer_group_2_with_acceptance_passes(self):
        result = validate.validate_listing(
            self._base(developer_group=2, developer_acceptance_confirmed=True)
        )
        check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        self.assertEqual(check["status"], "pass")

    def test_developer_group_1_passes_without_acceptance(self):
        result = validate.validate_listing(self._base(developer_group=1))
        check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        self.assertEqual(check["status"], "pass")

    def test_invalid_developer_group_value_fails(self):
        result = validate.validate_listing(self._base(developer_group=4))
        check = next(c for c in result["checks"] if c["rule"] == "developer_group_gate")
        self.assertEqual(check["status"], "fail")

    def test_primary_market_without_legal_entity_fails(self):
        result = validate.validate_listing(
            self._base(is_primary_market=True, developer_legal_entity=None)
        )
        check = next(c for c in result["checks"] if c["rule"] == "legal_block_present")
        self.assertEqual(check["status"], "fail")

    def test_primary_market_missing_legal_block_in_text_fails(self):
        result = validate.validate_listing(
            self._base(is_primary_market=True, developer_legal_entity="ООО «Тест-Инвест»")
        )
        check = next(c for c in result["checks"] if c["rule"] == "legal_block_present")
        self.assertEqual(check["status"], "fail")
        self.assertIn("юр. лицо застройщика", check["detail"])

    def test_primary_market_complete_legal_block_passes(self):
        result = validate.validate_listing(self._base(
            is_primary_market=True,
            developer_legal_entity="ООО «Тест-Инвест»",
            listing_text=(
                "2-комн. евро, 46,2 м², ипотека от 1 ₽/мес по программе застройщика. "
                "Продажа по ДДУ согласно ФЗ №214-ФЗ. Застройщик: ООО «Тест-Инвест». "
                "Проектная декларация — наш.дом.рф."
            ),
        ))
        check = next(c for c in result["checks"] if c["rule"] == "legal_block_present")
        self.assertEqual(check["status"], "pass")
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
