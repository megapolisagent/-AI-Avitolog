# -*- coding: utf-8 -*-
"""
Собирает feed.xml (схема — knowledge/avito-autoload-schema.md, рабочая, не официально
подтверждённая файлом от Avito, см. честную пометку там) из файлов data/lots/*.md,
написанных по формату data/lots/TEMPLATE.md.

Что НЕ делает:
  - не проверяет достоверность цены (это market_snapshot_analyzer.py + эталон ASTERUS)
  - не публикует фид никуда (нет хостинга — план по хостингу ещё не выбран владелицей,
    knowledge/agents_architecture.md, «План первого теста Автозагрузки v4»)
  - не подставляет реальные фото — Images остаётся placeholder, пока фото не даст застройщик/владелица

Лот с любым полем "[УТОЧНИТЬ]"/"[НЕТ ДАННЫХ]" — пропускается с явным предупреждением
в stderr, не попадает в фид с придуманным числом.

Исправлено (аудит Codex, 2026-09-02, CRITICAL C-1): <NewDevelopmentId> раньше заполнялся
текстовым названием ЖК (полем "complex") — это чужой тег, Avito ожидает там числовой ID
новостройки из своего каталога, не название. Подстановка текста уронила бы всю выгрузку
фида. Теперь это отдельное обязательное поле лота ("ID новостройки на Avito"), провалидировано
как строго цифровое; лот без него или с нечисловым значением пропускается тем же путём, что и
остальные обязательные поля — не публикуется с придуманным/подставленным значением.

Известный пробел (M1 Reviewer, 2026-09-02): knowledge/avito-autoload-schema.md перечисляет
в блоке <Ad> ещё 4 тега — HouseType, KitchenSpace, Title, Description — которых build_ad_xml
не генерирует. Не добавлены сознательно, не по недосмотру: HouseType/KitchenSpace нет как
полей в data/lots/TEMPLATE.md (добавить их — значит расширить шаблон входных данных, это
подпадает под Locked-паузу «никакого усложнения до первой опубликованной волны», DECISIONS.md
2026-09-01), а Title/Description — не сырые данные лота, а готовый текст карточки (заголовок и
описание для покупателя), который пишет Skill упаковки, не генератор фида — подставлять их
автоматически из полей TEMPLATE.md значило бы выдумывать текст. Если первая реальная выгрузка
фида (см. avito-autoload-schema.md, «Как эта схема реально проверяется») покажет, что Avito
требует эти теги обязательными — тогда расширять шаблон и функцию, не раньше.
"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LOTS_DIR = Path(__file__).parent.parent / "data" / "lots"
OUT_PATH = Path(__file__).parent.parent / "feed.xml"

PLACEHOLDER_MARKERS = ("[УТОЧНИТЬ]", "[НЕТ ДАННЫХ]")
PLACEHOLDER_PHOTO_DOMAIN = "placeholder.avitolog.local"

FIELD_MAP = {
    "ЖК / корпус / секция": "complex",
    "ID новостройки на Avito (NewDevelopmentId — число из личного кабинета Avito, раздел «Каталог новостроек», не название ЖК)": "new_development_id",
    "Планировка (студия/1к/2к евро и т.п.)": "planning",
    "Площадь, м²": "area",
    "Этаж": "floor",
    "Полная цена, ₽": "price",
    "Первоначальный взнос — сумма и %": "down_payment",
    "Ипотечная программа и ставка (если есть — семейная/базовая/субсидированная)": "mortgage",
    "Рассрочка от застройщика — условия, срок (если есть)": "installment",
    "Вид из окна / особенность лота (если есть — влияет на карточку и текст)": "feature",
}

ROOMS_FROM_PLANNING = {
    "студия": 0, "1к": 1, "1-к": 1, "2к": 2, "2-к": 2, "3к": 3, "3-к": 3, "4к": 4, "4-к": 4,
}


def parse_lot_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields = {}
    for line in text.splitlines():
        m = re.match(r"-\s*([^:]+):\s*(.*)", line.strip())
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        key = FIELD_MAP.get(label)
        if key:
            fields[key] = value
    return fields


def guess_rooms(planning: str) -> str:
    p = planning.lower()
    for token, n in ROOMS_FROM_PLANNING.items():
        if token in p:
            return str(n)
    return "[УТОЧНИТЬ]"


def has_placeholder(fields: dict) -> list:
    bad = []
    for k, v in fields.items():
        if not v or any(marker in v for marker in PLACEHOLDER_MARKERS):
            bad.append(k)
    required = ("complex", "new_development_id", "area", "floor", "price")
    for k in required:
        if k not in fields:
            bad.append(k + " (поле отсутствует)")
    ndid = fields.get("new_development_id", "")
    is_placeholder = any(marker in ndid for marker in PLACEHOLDER_MARKERS)
    if ndid and not is_placeholder and not re.fullmatch(r"\d+", ndid):
        bad.append(
            "new_development_id (не число — Avito ожидает числовой ID новостройки из "
            f"своего каталога, получено: {ndid!r})"
        )
    return bad


def price_to_int(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0


def extract_area(area_str: str) -> str:
    """Берёт только ведущее число площади — поле в data/lots/*.md может содержать
    примечание в скобках (напр. "46,2 (кухня-гостиная 17,5 м²)"), а тег <Square>
    схемы Avito требует голое число, не свободный текст."""
    m = re.match(r"[\d]+[.,]?[\d]*", area_str.replace(",", "."))
    return m.group(0) if m else ""


def build_ad_xml(lot_id: str, fields: dict) -> str:
    rooms = guess_rooms(fields.get("planning", ""))
    price = price_to_int(fields.get("price", "0"))
    area = extract_area(fields.get("area", ""))
    floor_raw = fields.get("floor", "")
    floor, floors = (floor_raw.split("/") + ["", ""])[:2]

    return f"""    <Ad>
        <Id>{escape(lot_id)}</Id>
        <Category>Квартиры</Category>
        <OperationType>Продам</OperationType>
        <MarketType>Новостройка</MarketType>
        <Floor>{escape(floor)}</Floor>
        <Floors>{escape(floors)}</Floors>
        <Rooms>{escape(rooms)}</Rooms>
        <Square>{escape(area)}</Square>
        <Price>{price}</Price>
        <Address>{escape(fields.get('complex', ''))}</Address>
        <NewDevelopmentId>{escape(fields.get('new_development_id', ''))}</NewDevelopmentId>
        <Status>Квартира</Status>
        <Images>
            <!-- ЗАГЛУШКА — заменить на реальные фото от застройщика перед реальной загрузкой -->
            <Image url="https://{PLACEHOLDER_PHOTO_DOMAIN}/photos/{escape(lot_id)}-01.jpg"/>
        </Images>
    </Ad>"""


def main():
    lot_files = sorted(LOTS_DIR.glob("2026-*.md"))
    if not lot_files:
        print("Нет файлов лотов в data/lots/ (кроме README.md/TEMPLATE.md) — фид будет пустым.", file=sys.stderr)

    ads = []
    for path in lot_files:
        lot_id = path.stem
        fields = parse_lot_file(path)
        bad = has_placeholder(fields)
        if bad:
            print(f"Пропущен {path.name}: не хватает/не заполнено — {bad}", file=sys.stderr)
            continue
        ads.append(build_ad_xml(lot_id, fields))

    if not ads:
        print("Ни один лот не прошёл проверку — фид записан пустым (старое содержимое не остаётся).", file=sys.stderr)

    body = ("\n".join(ads) + "\n") if ads else ""

    # Защита (C-4, аудит Codex 2026-09-02): пока Images ссылается на заглушку —
    # фид не должен уйти в реальную выгрузку Автозагрузки. Явная блокирующая
    # ошибка вместо тихой записи файла с нерабочими фото.
    if PLACEHOLDER_PHOTO_DOMAIN in body:
        print(
            f"ОШИБКА: в фиде остались заглушки фото ({PLACEHOLDER_PHOTO_DOMAIN}) — "
            f"feed.xml НЕ записан. Замените Images реальными фото перед генерацией.",
            file=sys.stderr,
        )
        sys.exit(1)

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<Ads formatVersion="3" target="Avito">\n' + body + "</Ads>\n"
    OUT_PATH.write_text(xml, encoding="utf-8")
    print(f"Записано лотов в фид: {len(ads)} -> {OUT_PATH}")


if __name__ == "__main__":
    main()
