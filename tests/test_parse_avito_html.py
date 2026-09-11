"""Тест scripts/parse_avito_html.py — детерминированный разбор title/даты, плюс извлечение
карточек из инлайн-фрагмента реальной разметки Авито (data-marker атрибуты, снятые с живой
сохранённой страницы по ЖК Алия, 2026-09-11 — не выдуманные)."""
import unittest

from _loader import load_module

parser = load_module("scripts/parse_avito_html.py", "parse_avito_html")


class TestRoomsAndArea(unittest.TestCase):
    def test_two_room_flat(self):
        self.assertEqual(parser.parse_rooms("2-к. квартира, 67,7 м², 19/28 эт."), 2)
        self.assertEqual(parser.parse_area("2-к. квартира, 67,7 м², 19/28 эт."), 67.7)

    def test_studio_has_zero_rooms(self):
        self.assertEqual(parser.parse_rooms("Квартира-студия, 25 м², 25/25 эт."), 0)

    def test_no_match_is_none_not_guessed(self):
        self.assertIsNone(parser.parse_rooms("Продам гараж"))
        self.assertIsNone(parser.parse_area("Продам гараж"))


class TestListingAge(unittest.TestCase):
    def test_today_and_yesterday(self):
        self.assertEqual(parser.parse_listing_age_days("сегодня"), 0)
        self.assertEqual(parser.parse_listing_age_days("вчера"), 1)

    def test_days_ago(self):
        self.assertEqual(parser.parse_listing_age_days("3 дня назад"), 3)
        self.assertEqual(parser.parse_listing_age_days("12 дней назад"), 12)

    def test_weeks_and_months_approximate_to_days(self):
        self.assertEqual(parser.parse_listing_age_days("2 недели назад"), 14)
        self.assertEqual(parser.parse_listing_age_days("1 месяц назад"), 30)

    def test_empty_or_unrecognized_is_none_not_zero(self):
        self.assertIsNone(parser.parse_listing_age_days(""))
        self.assertIsNone(parser.parse_listing_age_days("поднято сегодня в топ"))


class TestExtractListings(unittest.TestCase):
    """Фрагмент — реальные data-marker блоки с сохранённой страницы по ЖК Алия
    (структура снята вручную 2026-09-11, не придумана)."""

    HTML = """
    <div data-marker="catalog-serp">
      <div data-marker="item" data-item-id="111" itemscope itemtype="http://schema.org/Product">
        <a data-marker="item-title" title="2-к. квартира, 60 м², 5/10 эт. в Москве" href="https://www.avito.ru/x/2-k._kvartira_60_m_510_et._111?context=abc">2-к.</a>
        <p data-marker="item-price" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="20000000">
          <span data-marker="item-price-value">20 000 000 &#8381;</span>
        </p>
        <div data-marker="item-address">Ул. Тестовая, 1 Спартак, 10 мин.</div>
        <p data-marker="item-specific-params">Без залога</p>
        <p data-marker="item-date">5 дней назад</p>
      </div>
    </div>
    """

    def test_extracts_core_fields(self):
        listings = parser.extract_listings(self.HTML)
        self.assertEqual(len(listings), 1)
        lot = listings[0]
        self.assertEqual(lot["item_id"], "111")
        self.assertEqual(lot["price"], 20_000_000)
        self.assertEqual(lot["area"], 60.0)
        self.assertEqual(lot["rooms"], 2)
        self.assertEqual(lot["listing_age_days"], 5)
        self.assertEqual(lot["url"], "https://www.avito.ru/x/2-k._kvartira_60_m_510_et._111")
        self.assertIn("Тестовая", lot["address"])

    def test_seller_type_and_renovation_left_for_agent_judgment(self):
        lot = parser.extract_listings(self.HTML)[0]
        self.assertIsNone(lot["seller_type"])
        self.assertIsNone(lot["renovation"])


if __name__ == "__main__":
    unittest.main()
