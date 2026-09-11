# -*- coding: utf-8 -*-
"""
Разбирает сохранённую владелицей страницу поиска Авито ("Сохранить как -> Веб-страница
целиком") в сырой снимок лотов — тот же вход, что и market-analyst обычно собирает через
Apify, только бесплатно и без риска блокировки 403/429 (см. .claude/agents/market-analyst.md,
"Вариант А"). Найдено и проверено на реальной выдаче по ЖК Алия, 2026-09-11.

Что делает и чего не делает:
  - Достаёт то, что физически есть в HTML страницы ВЫДАЧИ: заголовок, цену, площадь,
    комнатность, адрес+короткие параметры, дату публикации, ссылку, превью текста
    продавца (см. ниже про description).
  - `description` — реальный текст продавца из `meta[itemprop="description"]` каждой
    карточки, НЕ выдумка и НЕ пусто (ошибка первой версии этого скрипта, найдена и
    исправлена 2026-09-11 — владелица указала на реальные лоты в HTML, где текст
    физически есть). Но обрезан Авито ровно до 256 символов (SEO-сниппет, не полное
    объявление) — обрывается на полуслове почти всегда. Это открывающий крючок текста,
    не вся боль/аргументация продавца целиком — честный, но частичный вход для
    `pain-point-extractor`, не замена детальному Apify-кроулингу, если нужен текст целиком.
  - НЕ определяет seller_type/renovation — это осознанно оставлено полем для суждения
    market-analyst (см. его SKILL, п.5/6 "единственное место, где нужно суждение") на
    основании title/address/params/description этого же лота, не считается тут вслепую.

Формат вывода — список объектов, полностью совместимый со схемой
scripts/market_snapshot_analyzer.py (docstring в начале того файла): title, price, area,
rooms, seller_type, renovation, listing_age_days, url, description — плюс address/params/
date_text как сырой текст-подсказка для классификации агентом.

Запуск:
    pip install beautifulsoup4 lxml
    python scripts/parse_avito_html.py <путь_к_сохранённому.html> <путь_к_выходному.json>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

# Виндовая консоль по умолчанию открывает stdout в cp1251 — путь/эмодзи в имени сохранённого
# файла валит print() с UnicodeEncodeError (тот же баг, что уже был найден и исправлен
# 2026-09-10 в market_snapshot_analyzer.py).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOMS_RE = re.compile(r"(\d+)-к\.")
STUDIO_RE = re.compile(r"студия", re.IGNORECASE)
AREA_RE = re.compile(r"(\d+(?:,\d+)?)\s*м²")

# "N чего-то назад" -> множитель в дни. Приблизительно (часы/минуты округляются до 0 дней) —
# для целей staleness-порога (config.json: stale_listing_days_threshold, обычно 60 дней)
# точность до дня не нужна.
AGE_UNIT_TO_DAYS = {
    "минут": 0, "минуту": 0, "минуты": 0,
    "час": 0, "часа": 0, "часов": 0,
    "день": 1, "дня": 1, "дней": 1,
    "неделю": 7, "недели": 7, "недель": 7,
    "месяц": 30, "месяца": 30, "месяцев": 30,
}
AGE_RELATIVE_RE = re.compile(r"(\d+)\s+(\S+)\s+назад", re.IGNORECASE)


def clean_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_rooms(title: str) -> int | None:
    m = ROOMS_RE.search(title)
    if m:
        return int(m.group(1))
    if STUDIO_RE.search(title):
        return 0
    return None


def parse_area(title: str) -> float | None:
    m = AREA_RE.search(title)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_listing_age_days(date_text: str) -> int | None:
    text = date_text.strip().lower()
    if not text:
        return None
    if text.startswith("сегодня"):
        return 0
    if text.startswith("вчера"):
        return 1
    m = AGE_RELATIVE_RE.search(text)
    if not m:
        return None
    count, unit = int(m.group(1)), m.group(2)
    per_unit = AGE_UNIT_TO_DAYS.get(unit)
    if per_unit is None:
        return None
    return count * per_unit


def extract_listings(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "lxml")
    listings = []

    for item in soup.find_all("div", attrs={"data-marker": "item"}):
        title_link = item.find("a", attrs={"data-marker": "item-title"})
        price_meta = item.find("meta", attrs={"itemprop": "price"})
        description_meta = item.find("meta", attrs={"itemprop": "description"})
        address = item.find(attrs={"data-marker": "item-address"})
        params = item.find(attrs={"data-marker": "item-specific-params"})
        date = item.find(attrs={"data-marker": "item-date"})

        title = title_link.get("title", clean_text(title_link)) if title_link else ""
        url = title_link["href"].split("?")[0] if title_link and title_link.get("href") else ""
        date_text = clean_text(date)

        listings.append({
            "item_id": item.get("data-item-id", ""),
            "title": title,
            "price": int(price_meta["content"]) if price_meta and price_meta.get("content") else None,
            "area": parse_area(title),
            "rooms": parse_rooms(title),
            "seller_type": None,
            "renovation": None,
            "listing_age_days": parse_listing_age_days(date_text),
            "url": url,
            "description": description_meta.get("content", "").strip() if description_meta else "",
            "address": clean_text(address),
            "params": clean_text(params),
            "date_text": date_text,
        })
    return listings


def main() -> None:
    if len(sys.argv) != 3:
        print("Использование: python parse_avito_html.py <сохранённый.html> <выход.json>")
        sys.exit(1)

    html_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    if not html_path.exists():
        print(f"Файл не найден: {html_path}")
        sys.exit(1)

    listings = extract_listings(html_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)

    print(f"Разобрано лотов: {len(listings)}")
    missing_price = sum(1 for l in listings if l["price"] is None)
    missing_area = sum(1 for l in listings if l["area"] is None)
    if missing_price or missing_area:
        print(f"Внимание: без цены — {missing_price}, без площади — {missing_area} (страница могла измениться)")
    print(f"seller_type/renovation оставлены пустыми — классифицировать по title/address/params/description, не считать это готовым файлом для market_snapshot_analyzer.py")
    print(f"description — превью текста продавца, обрезано Авито до 256 символов, не весь текст объявления")
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    main()
