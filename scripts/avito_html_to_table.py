# -*- coding: utf-8 -*-
"""
Разбирает сохранённую вручную страницу выдачи Авито и ДОБАВЛЯЕТ карточки в одну постоянную
таблицу — ../../Avito Market Data/avito_listings.xlsx (лист "Все объявления"). Таблица живёт
вне этого репозитория намеренно (решение владелицы 2026-09-14) — растущие данные не должны
раздувать память самого Авитолога, здесь остаётся только инструмент.

Финальный регламент источников данных (владелица, 2026-09-14, дословно):
  - Строго локальный контур: только содержимое переданного HTML-файла. Ни одного сетевого
    запроса, ни одного перехода по ссылке карточки, ни обращения к сайтам девелоперов.
  - Правило пустых значений: нет данных в видимом блоке карточки — не ищем, не выдумываем,
    ставим "НЕТ ДАННЫХ".

Кто продавец — версия 2 (2026-09-14, после разбора реального HTML).
Версия 1 искала по ключевым словам в тексте описания — провалилась: у 49 из 50 карточек
"НЕТ ДАННЫХ", потому что маркетинговый текст застройщика не содержит слова "застройщик".
Реальный источник — блок sellerInfo/userInfoStep у каждой карточки: имя продавца + ссылка
на профиль .../brands/<id> + счётчик "N завершённых объявлений". Проверено вручную на двух
настоящих сохранённых страницах ЖК Алиа (файлы владелицы, 2026-09-14):
  - id-хэш без "i" (например, у "Астерус") — подтверждённый бренд-аккаунт.
  - id вида "i<цифры>" — обычный профиль, но так помечены и крупные агентства ("Этажи
    Москва"), не только частники — по одному id тип не различить.
Правило классификации (эвристика, не 100% гарантия — колонка "Основание для продавца"
всегда фиксирует, по какому признаку принято решение, для проверки в самой таблице):
  1. Имя продавца похоже на компанию (содержит "агентство"/"недвижимост"/"риэлт"/"риелт"/
     начинается с "АН ") -> Агентство.
  2. id — бренд-хэш, и это имя даёт наибольшую долю карточек внутри одного ЖК в этом
     прогоне (порог DEVELOPER_MIN_SHARE) -> Застройщик (в проверенных файлах так и есть:
     "Астерус" — 39 из 49 карточек ЖК Алиа).
  3. id — бренд-хэш, но доля в этом ЖК меньше порога -> Агентство (похоже на партнёрское
     агентство, продающее несколько лотов, не на самого застройщика).
  4. id — обычный профиль "i<цифры>", похоже на личное имя, "N завершённых объявлений"
     ниже AGENT_LISTINGS_THRESHOLD -> Собственник.
  5. id — обычный профиль, но много завершённых объявлений -> Агентство (частный риэлтор).
  6. Блок продавца не найден вовсе -> НЕТ ДАННЫХ.

Текст объявления: у продавца "Застройщик" сохраняется только у первой карточки одного ЖК за
этот прогон — у "Собственник"/"Агентство"/"НЕТ ДАННЫХ" сохраняется всегда полностью.

Дедупликация: ключ строки — (Номер объявления, Дата выгрузки). Один и тот же лот, снятый в
разные даты, остаётся в таблице несколько раз — это история цены, не дубль.

Запуск:
    pip install -r requirements.txt
    python scripts/avito_html_to_table.py <путь_к_сохранённому.html>
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

NO_DATA = "НЕТ ДАННЫХ"
SHEET_NAME = "Все объявления"
TABLE_PATH = Path(__file__).resolve().parent.parent.parent / "Avito Market Data" / "avito_listings.xlsx"

DEVELOPER_MIN_SHARE = 0.30   # доля карточек одного ЖК от одного бренд-продавца, чтобы считать его застройщиком
AGENT_LISTINGS_THRESHOLD = 15  # "завершённых объявлений" выше — считаем профессиональным агентом, не собственником

HEADERS = [
    "Дата выгрузки", "Жилой комплекс", "Комнатность", "Площадь, м²",
    "Этаж / Этажность", "Цена в объявлении, ₽", "Цена за м², ₽",
    "Кто продавец", "Основание для продавца", "Тип предложения",
    "Условия сделки", "Особенности комнат", "Текст объявления",
    "Ссылка на объект", "Номер объявления", "Файл-источник",
    "Физический дубль",
]

ROOMS_RE = re.compile(r"(\d+)-к\.")
STUDIO_RE = re.compile(r"студия", re.IGNORECASE)
AREA_RE = re.compile(r"(\d+(?:,\d+)?)\s*м²")
FLOOR_RE = re.compile(r"(\d+)/(\d+)\s*эт\.")
ZHK_QUOTED_RE = re.compile(r"ЖК\s*[«\"]([^»\"]+)[»\"]")
ZHK_PLAIN_RE = re.compile(r"(?:ЖК|жилом районе|квартале)\s+([А-ЯЁ][а-яё]+(?:\s+[А-Яа-яЁё][а-яё]*)?|Alia)")
# слова, которые regex иногда прихватывает вторым словом по ошибке (предлоги/связки после
# названия ЖК — "ЖК Алия НА 20 этаже", "квартал Алиа ПЕРВАЯ линия") — это не часть названия
ZHK_NAME_STOPWORDS = {"на", "в", "по", "с", "у", "от", "для", "первая", "первой", "линия", "линии"}

BRAND_HREF_RE = re.compile(r"avito\.ru/brands/([A-Za-z0-9]+)\?")
COMPLETED_LISTINGS_RE = re.compile(r"(\d+)\s+завершённ")

AGENCY_NAME_HINTS = ["агентство", "недвижимост", "риэлт", "риелт", "estate", "realty"]

# "переуступка" сюда намеренно не входит — это уже определяется отдельно в "Тип предложения",
# дублировать тот же факт другими словами в "Условия сделки" не нужно (найдено 2026-09-14:
# 53 из 55 строк были пустыми, а где заполнено — просто пересказывало "Тип предложения").
DEAL_TERMS = ["ДДУ", "ипотека", "ипотеку", "рассрочка", "рассрочку", "залог банка",
              "военная ипотека", "семейная ипотека"]
ROOM_FEATURE_TERMS = ["кухня-гостиная", "мастер-спальня", "изолированные комнаты", "смежные комнаты",
                       "гардеробная", "лоджия", "панорамные окна", "вид на"]


def clean_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def find_keyword(text: str, terms: list[str]) -> str | None:
    low = text.lower()
    for term in terms:
        if term.lower() in low:
            return term
    return None


def parse_rooms(title: str) -> str:
    m = ROOMS_RE.search(title)
    if m:
        return m.group(1)
    if STUDIO_RE.search(title):
        return "Студия"
    return NO_DATA


def parse_area(title: str) -> str:
    m = AREA_RE.search(title)
    return m.group(1).replace(",", ".") if m else NO_DATA


def parse_floor(title: str) -> str:
    m = FLOOR_RE.search(title)
    return f"{m.group(1)}/{m.group(2)}" if m else NO_DATA


def parse_zhk(*texts: str) -> str:
    for text in texts:
        m = ZHK_QUOTED_RE.search(text) or ZHK_PLAIN_RE.search(text)
        if m:
            words = [w for w in m.group(1).strip().split() if w.lower() not in ZHK_NAME_STOPWORDS]
            if not words:
                continue
            name = " ".join(w.capitalize() for w in words)
            return "Алиа" if name.lower() in ("alia", "алия", "алиа") else name
    return NO_DATA


def looks_like_agency_name(name: str) -> bool:
    low = name.lower()
    return low.startswith("ан ") or any(h in low for h in AGENCY_NAME_HINTS)


def extract_seller_block(item) -> tuple[str, str, int | None]:
    """Возвращает (имя_продавца, id_продавца, число_завершённых_объявлений) — НЕТ ДАННЫХ,
    если блока sellerInfo/brands-ссылки в этой карточке нет вообще."""
    link = item.find("a", href=BRAND_HREF_RE)
    if link is None:
        return NO_DATA, NO_DATA, None
    m = BRAND_HREF_RE.search(link.get("href", ""))
    seller_id = m.group(1) if m else NO_DATA
    name_node = link.find("p")
    seller_name = clean_text(name_node) if name_node else clean_text(link)
    surrounding_text = item.get_text(" ", strip=True)
    count_m = COMPLETED_LISTINGS_RE.search(surrounding_text)
    completed = int(count_m.group(1)) if count_m else None
    return seller_name or NO_DATA, seller_id, completed


def is_brand_account(seller_id: str) -> bool:
    return seller_id != NO_DATA and not seller_id.startswith("i")


def classify_offer_type(text: str, seller_type: str) -> str:
    low = text.lower()
    if "переуступ" in low:
        return "Стройка по переуступке"
    if seller_type == "Застройщик":
        return "Стройка от девелопера"
    if any(w in low for w in ("ключи", "собственность", "вторичн", "готов дом", "сдан дом")):
        return "Готовый дом"
    return NO_DATA


def find_deal_terms(text: str) -> str:
    low = text.lower()
    found = [t for t in DEAL_TERMS if t.lower() in low]
    return ", ".join(sorted(set(found))) if found else NO_DATA


def find_room_features(text: str) -> str:
    low = text.lower()
    found = [t for t in ROOM_FEATURE_TERMS if t.lower() in low]
    return ", ".join(sorted(set(found))) if found else NO_DATA


def extract_raw_cards(html_text: str) -> list[dict]:
    """Первый проход: достаёт всё, что можно взять с одной карточки в изоляции — без
    классификации продавца, для неё нужно видеть распределение по всему ЖК сразу."""
    soup = BeautifulSoup(html_text, "lxml")
    cards = []
    for item in soup.find_all("div", attrs={"data-marker": "item"}):
        title_link = item.find("a", attrs={"data-marker": "item-title"})
        price_meta = item.find("meta", attrs={"itemprop": "price"})
        description_meta = item.find("meta", attrs={"itemprop": "description"})
        address_node = item.find(attrs={"data-marker": "item-address"})

        title = title_link.get("title", clean_text(title_link)) if title_link else ""
        url = title_link["href"].split("?")[0] if title_link and title_link.get("href") else ""
        if url and not url.startswith("http"):
            url = f"https://www.avito.ru{url}"
        item_id = item.get("data-item-id", "") or NO_DATA
        description = description_meta.get("content", "").strip() if description_meta else ""
        address = clean_text(address_node)

        price = int(price_meta["content"]) if price_meta and price_meta.get("content") else None
        area_str = parse_area(title)
        area = float(area_str) if area_str != NO_DATA else None

        seller_name, seller_id, completed = extract_seller_block(item)
        zhk = parse_zhk(address, title, description)

        cards.append({
            "zhk": zhk,
            "title": title,
            "description": description,
            "address": address,
            "url": url,
            "item_id": item_id,
            "price": price,
            "area_str": area_str,
            "area": area,
            "seller_name": seller_name,
            "seller_id": seller_id,
            "completed": completed,
        })
    return cards


def classify_sellers(cards: list[dict]) -> None:
    """Второй проход: для каждого ЖК смотрит, какой бренд-продавец даёт наибольшую долю
    карточек — это и есть застройщик (мутирует cards на месте, добавляя seller_type/basis)."""
    by_zhk: dict[str, list[dict]] = {}
    for c in cards:
        by_zhk.setdefault(c["zhk"], []).append(c)

    developer_by_zhk: dict[str, str] = {}
    for zhk, group in by_zhk.items():
        brand_names = [c["seller_name"] for c in group if is_brand_account(c["seller_id"])]
        if not brand_names:
            continue
        name, count = Counter(brand_names).most_common(1)[0]
        if count / len(group) >= DEVELOPER_MIN_SHARE:
            developer_by_zhk[zhk] = name

    for c in cards:
        name, seller_id, completed = c["seller_name"], c["seller_id"], c["completed"]
        if name == NO_DATA:
            c["seller_type"], c["seller_basis"] = NO_DATA, NO_DATA
            continue
        if looks_like_agency_name(name):
            c["seller_type"] = "Агентство"
            c["seller_basis"] = f'название "{name}" похоже на компанию'
            continue
        if is_brand_account(seller_id):
            if developer_by_zhk.get(c["zhk"]) == name:
                c["seller_type"] = "Застройщик"
                c["seller_basis"] = f'"{name}" — большинство карточек ЖК "{c["zhk"]}" в этом прогоне'
            else:
                c["seller_type"] = "Агентство"
                c["seller_basis"] = f'"{name}" — бренд-аккаунт, но не основной продавец ЖК "{c["zhk"]}"'
            continue
        if completed is not None and completed >= AGENT_LISTINGS_THRESHOLD:
            c["seller_type"] = "Агентство"
            c["seller_basis"] = f'{completed} завершённых объявлений у "{name}" — похоже на частного риэлтора'
        else:
            c["seller_type"] = "Собственник"
            c["seller_basis"] = f'личный профиль "{name}"' + (
                f", {completed} завершённых объявлений" if completed is not None else ""
            )


def build_rows(cards: list[dict], source_file: str, captured_at: str) -> list[dict]:
    """Текст объявления: версия 2 (2026-09-14, после разбора реального HTML). Версия 1 стирала
    текст у всех карточек "Застройщик", кроме первой в ЖК — предположение "у застройщика
    везде один шаблон" оказалось неверным: 12 непустых текстов дали 11 РАЗНЫХ (партнёры/агенты
    перепродают от застройщика и пишут свой текст) — стирали реальный уникальный текст
    примерно у 28 из 39 строк. Теперь схлопывается только буквальное совпадение строка в
    строку, независимо от того, кто продавец."""
    rows = []
    text_seen: set[str] = set()
    for c in cards:
        price_per_m2 = round(c["price"] / c["area"]) if c["price"] and c["area"] else NO_DATA
        combined_for_search = f'{c["title"]} {c["description"]}'

        listing_text = c["description"] if c["description"] else NO_DATA
        if listing_text != NO_DATA:
            if listing_text in text_seen:
                listing_text = ""
            else:
                text_seen.add(listing_text)

        rows.append({
            "Дата выгрузки": captured_at,
            "Жилой комплекс": c["zhk"],
            "Комнатность": parse_rooms(c["title"]),
            "Площадь, м²": c["area_str"],
            "Этаж / Этажность": parse_floor(c["title"]),
            "Цена в объявлении, ₽": c["price"] if c["price"] is not None else NO_DATA,
            "Цена за м², ₽": price_per_m2,
            "Кто продавец": c["seller_type"],
            "Основание для продавца": c["seller_basis"],
            "Тип предложения": classify_offer_type(combined_for_search, c["seller_type"]),
            "Условия сделки": find_deal_terms(combined_for_search),
            "Особенности комнат": find_room_features(combined_for_search),
            "Текст объявления": listing_text,
            "Ссылка на объект": c["url"] if c["url"] else NO_DATA,
            "Номер объявления": c["item_id"],
            "Файл-источник": source_file,
            "Физический дубль": "",  # пересчитывается по всей таблице после записи, см. flag_physical_duplicates
        })
    return rows


def extract_listings(html_text: str, source_file: str, captured_at: str, zhk_override: str | None = None) -> list[dict]:
    cards = extract_raw_cards(html_text)
    if zhk_override:
        for c in cards:
            c["zhk"] = zhk_override
    classify_sellers(cards)
    return build_rows(cards, source_file, captured_at)


def physical_key(row_values: list) -> tuple | None:
    """(ЖК, Комнатность, Площадь, Этаж/Этажность) — один и тот же физический объект,
    выставленный разными продавцами (например, собственником и агентством параллельно),
    получает один и тот же ключ. НЕТ ДАННЫХ по ЖК не склеиваем — слишком велик риск
    случайно смешать разные жилые комплексы с одинаковой планировкой."""
    zhk = row_values[HEADERS.index("Жилой комплекс")]
    if zhk == NO_DATA:
        return None
    rooms = row_values[HEADERS.index("Комнатность")]
    area = row_values[HEADERS.index("Площадь, м²")]
    floor = row_values[HEADERS.index("Этаж / Этажность")]
    if NO_DATA in (rooms, area, floor):
        return None
    return (zhk, rooms, area, floor)


def flag_physical_duplicates(ws) -> None:
    """Владелица, 2026-09-14: один и тот же лот может попасть в выгрузку под двумя разными
    ID Авито (собственник и агентство продают одну и ту же квартиру параллельно) — ID Авито
    для этого не годится, нужен "физический паспорт" объекта. Помечает такие строки, не
    удаляет — обе карточки остаются видны (разные продавцы, разная цена/текст — это реальный
    маркетинговый сигнал), просто явно подписано, что это, вероятно, один и тот же объект."""
    data = [list(row) for row in ws.iter_rows(min_row=2, values_only=True)]
    id_idx = HEADERS.index("Номер объявления")
    dup_idx = HEADERS.index("Физический дубль")

    groups: dict[tuple, list[str]] = {}
    for row in data:
        key = physical_key(row)
        if key is not None:
            groups.setdefault(key, []).append(str(row[id_idx]))

    for row in data:
        key = physical_key(row)
        # dict.fromkeys вместо set — убирает повторы (например, один и тот же ID Авито,
        # если этот же лот попал в таблицу дважды с разных дат выгрузки), сохраняя порядок
        other_ids = list(dict.fromkeys(i for i in groups.get(key, []) if i != str(row[id_idx]))) if key else []
        row[dup_idx] = f"ДА, ещё {len(other_ids)}: {', '.join(other_ids)}" if other_ids else "НЕТ"

    for row_num, row in enumerate(data, start=2):
        ws.cell(row=row_num, column=dup_idx + 1, value=row[dup_idx])


def load_or_create_table(path: Path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME
        ws.append(HEADERS)
        return wb, ws, set()

    wb = load_workbook(path)
    ws = wb[SHEET_NAME]
    existing_keys = set()
    id_col = HEADERS.index("Номер объявления")
    date_col = HEADERS.index("Дата выгрузки")
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[id_col] is not None:
            existing_keys.add((str(row[id_col]), str(row[date_col])))
    return wb, ws, existing_keys


def autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in column_cells)
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(length + 2, 60)


def rewrite_sorted_with_hyperlinks(ws) -> None:
    data = [list(row) for row in ws.iter_rows(min_row=2, values_only=True)]
    zhk_idx = HEADERS.index("Жилой комплекс")
    date_idx = HEADERS.index("Дата выгрузки")
    url_idx = HEADERS.index("Ссылка на объект")
    data.sort(key=lambda r: (str(r[zhk_idx]), str(r[date_idx])))
    for row_num, row in enumerate(data, start=2):
        for col_num, value in enumerate(row, start=1):
            ws.cell(row=row_num, column=col_num, value=value)
        url = row[url_idx]
        cell = ws.cell(row=row_num, column=url_idx + 1)
        if url and url != NO_DATA:
            cell.hyperlink = url
            cell.style = "Hyperlink"


SALE_MIN_PRICE = 1_000_000  # ниже этого — считаем ценой аренды в месяц, не ценой продажи


def passes_filters(row: dict, rooms: str | None, deal: str | None) -> bool:
    if rooms is not None and str(row["Комнатность"]) != rooms:
        return False
    if deal is not None:
        price = row["Цена в объявлении, ₽"]
        if price == NO_DATA:
            return False
        if deal == "sale" and price < SALE_MIN_PRICE:
            return False
        if deal == "rent" and price >= SALE_MIN_PRICE:
            return False
    return True


def build_arg_parser() -> "argparse.ArgumentParser":
    import argparse
    p = argparse.ArgumentParser(
        description="HTML-выдача Авито -> постоянная таблица avito_listings.xlsx"
    )
    p.add_argument("html_path", help="путь к сохранённому HTML-файлу")
    p.add_argument("--force-zhk", metavar="НАЗВАНИЕ",
                    help='задать Жилой комплекс для ВСЕХ карточек файла вручную (используйте, '
                         'когда точно знаете, что весь файл — про один ЖК, вместо угадывания по тексту)')
    p.add_argument("--only-zhk", metavar="НАЗВАНИЕ",
                    help='оставить в таблице только карточки с этим Жилым комплексом, остальное '
                         '(другие районы) отбросить, не записывая в таблицу')
    p.add_argument("--rooms", metavar="N",
                    help='оставить только карточки с такой комнатностью (например: 2, или "Студия")')
    p.add_argument("--deal", choices=["sale", "rent"],
                    help='оставить только "sale" (продажа, цена от %d ₽) или "rent" (аренда, '
                         'цена ниже) — граница фиксированная, не подбирается под конкретный файл' % SALE_MIN_PRICE)
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    html_path = Path(args.html_path)
    if not html_path.exists():
        print(f"Файл не найден: {html_path}")
        sys.exit(1)

    captured_at = datetime.fromtimestamp(html_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    all_rows = extract_listings(
        html_path.read_text(encoding="utf-8", errors="ignore"), html_path.name, captured_at, args.force_zhk
    )

    rows = [r for r in all_rows if r["Жилой комплекс"] == args.only_zhk] if args.only_zhk else all_rows
    dropped_by_zhk = len(all_rows) - len(rows)

    kept = [r for r in rows if passes_filters(r, args.rooms, args.deal)]
    dropped_by_filter = len(rows) - len(kept)
    rows = kept

    wb, ws, existing_keys = load_or_create_table(TABLE_PATH)

    added, skipped_dupe = 0, 0
    for row in rows:
        key = (str(row["Номер объявления"]), str(row["Дата выгрузки"]))
        if key in existing_keys:
            skipped_dupe += 1
            continue
        ws.append([row[h] for h in HEADERS])
        existing_keys.add(key)
        added += 1

    rewrite_sorted_with_hyperlinks(ws)
    flag_physical_duplicates(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    autosize_columns(ws)

    wb.save(TABLE_PATH)

    dup_idx = HEADERS.index("Физический дубль")
    physical_dupes = sum(
        1 for row in ws.iter_rows(min_row=2, values_only=True) if str(row[dup_idx]).startswith("ДА")
    )

    seller_counts = Counter(r["Кто продавец"] for r in rows)
    print(f"Разобрано карточек в файле: {len(all_rows)}")
    if args.only_zhk:
        print(f'Отброшено (Жилой комплекс != "{args.only_zhk}"): {dropped_by_zhk}')
    if args.rooms or args.deal:
        print(f"Отброшено фильтром --rooms/--deal: {dropped_by_filter}")
    print(f"Добавлено новых строк: {added}")
    print(f"Пропущено как дубликат (тот же номер + та же дата выгрузки уже в таблице): {skipped_dupe}")
    print(f"Кто продавец: {dict(seller_counts)}")
    print(f"Физических дублей по всей таблице (ЖК+комнатность+площадь+этаж совпали): {physical_dupes}")
    print(f"Таблица: {TABLE_PATH}")


if __name__ == "__main__":
    main()
