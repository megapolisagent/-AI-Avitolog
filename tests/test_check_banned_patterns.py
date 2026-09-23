"""Тест listing-copywriter/tools/check_banned_patterns.py — детерминированная проверка
банк-листа и канцелярита, не полагаться на то, что модель сама заметит штамп."""
import unittest

from _loader import load_module

checker = load_module(".claude/skills/listing-copywriter/tools/check_banned_patterns.py", "check_banned_patterns")


class TestBannedWords(unittest.TestCase):
    def test_clean_text_passes(self):
        result = checker.check_text(
            "2-к квартира, 46.2 м², ЖК ALIA. Кухня с видом на парк. "
            "Напишите +, пришлю расчёт по вашей ставке."
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["banned_words"], [])
        self.assertEqual(result["kantselyarit"], [])

    def test_exact_banned_word_detected(self):
        result = checker.check_text("Прекрасная квартира ждёт вас.")
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["banned_words"]), 1)

    def test_declined_multiword_phrase_detected(self):
        # Реальный найденный баг: точная фраза "развитая инфраструктура" не ловила
        # склонение "развитой инфраструктурой" — регэксп теперь на стемах.
        result = checker.check_text("Квартира с развитой инфраструктурой во дворе.")
        self.assertFalse(result["passed"])
        self.assertTrue(any("инфраструктур" in b["pattern"] for b in result["banned_words"]))


class TestKantselyarit(unittest.TestCase):
    def test_kantselyarit_phrase_detected(self):
        result = checker.check_text("В связи с тем что спрос растёт, цены выросли.")
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["kantselyarit"]), 1)

    def test_dannaya_word_detected(self):
        result = checker.check_text("Данная квартира продаётся быстро.")
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
