# -*- coding: utf-8 -*-
"""
Разбирает data/market_raw/market_snapshot.json — файл среза рынка (любого происхождения,
источник не проверяется этим скриптом, это забота того, кто кладёт файл в папку).

Ожидаемая структура файла — список объектов:
  {
    "title": str,
    "price": число (руб, полная цена),
    "area": число (м²),
    "rooms": int (комнатность — 1/2/3; поле добавлено Engineer сверх исходного запроса
                   владельца, без него группировка "по комнатности" технически невозможна;
                   если поля нет в файле, лот попадает в группу "не определено", не отбрасывается),
    "seller_type": "developer" | "agent" | "owner" (расширено 2026-09-10 владельцем —
                   на Avito это три разных продавца, не два; лот без этого поля не отбрасывается,
                   попадает в счётчик "не определено" в отчёте),
    "renovation": "with" | "without" | None (с ремонтом / без / не удалось определить из текста
                   объявления — поле добавлено 2026-09-10, не заполнено — не значит "без ремонта"),
    "listing_age_days": int | None (только для seller_type agent/owner — для developer это поле
                   игнорируется расчётом целиком, застройщик поднимает карточку независимо от
                   спроса, возраст там ничего не говорит; добавлено 2026-09-10),
    "url": str
  }

Что делает:
  1. Группирует лоты по rooms.
  2. Считает цену за м² для каждого лота.
  3. Медиану по группе — из лотов seller_type == "owner" (сравнение "самих себя" вторички
     с самой собой).

Сравнение с эталоном цены застройщика убрано 2026-09-17 по решению владелицы вместе
со всем остальным по прежнему ЖК — эталона для сравнения сейчас физически нет.
Появится новый застройщик/ЖК — добавлять эталон и логику "подозрительно занижено"
заново отдельным решением, не восстанавливать прежние цифры (см. git log на эту дату,
если понадобится форма кода).

Честно: скрипт не проверяет, откуда взялся market_snapshot.json, не умеет отличить
настоящее объявление от подставного номера в самом файле — это ответственность
того, кто наполняет файл, не этого скрипта.
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Виндовая консоль по умолчанию открывает stdout в cp1251 — символ ₽ в median_note
# ниже валит скрипт с UnicodeEncodeError при первом же реальном запуске (баг обнаружен
# 2026-09-10 при первом тестовом прогоне, был в коде и раньше, просто не запускался).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Закрытый словарь значений seller_type/renovation, которые обязан присылать market-analyst
# (.claude/agents/market-analyst.md) — продублирован здесь как FORM для механической проверки
# (pipeline-architecture.md §6): missing (поле не указано) и invalid (указано вне словаря,
# опечатка/сбой субагента) — два разных случая, не одна и та же "не определено" корзина.
SELLER_TYPE_FORM = {"developer", "agent", "owner"}
RENOVATION_FORM = {"with", "without"}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def load_config() -> dict:
    """Пороги — только из config.json (`pipeline-architecture.md` §4), не хардкод в коде.
    Файл отсутствует/битый — падаем с понятной ошибкой, не подставляем тихий дефолт."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["market_analysis"]


CONFIG = load_config()
DUPLICATE_AREA_TOLERANCE_M2 = CONFIG["duplicate_area_tolerance_m2"]
DUPLICATE_PRICE_TOLERANCE_PCT = CONFIG["duplicate_price_tolerance_pct"]
STALE_LISTING_DAYS_THRESHOLD = CONFIG["stale_listing_days_threshold"]


def detect_duplicates(lots: list) -> list:
    """Помечает вторичные лоты (agent/owner), которые с высокой вероятностью — одна и та же
    квартира у разных продавцов: те же rooms, площадь и цена в пределах допуска из config.json.
    Не трогает developer-лоты — застройщик не дублирует чужие объявления.

    Правило-детерминированное (Script-First — не поручать эту классификацию агенту, здесь
    не нужно суждение, только арифметика допуска)."""
    candidates = [
        (i, lot) for i, lot in enumerate(lots)
        if lot.get("seller_type") in ("agent", "owner") and lot.get("area") and lot.get("price") is not None
    ]
    duplicate_of = {}  # index -> index первого лота в группе дублей
    for a_idx in range(len(candidates)):
        i, a = candidates[a_idx]
        if i in duplicate_of:
            continue
        for b_idx in range(a_idx + 1, len(candidates)):
            j, b = candidates[b_idx]
            if j in duplicate_of:
                continue
            if a.get("rooms") != b.get("rooms"):
                continue
            area_close = abs(a["area"] - b["area"]) <= DUPLICATE_AREA_TOLERANCE_M2
            price_close = abs(a["price"] - b["price"]) <= b["price"] * DUPLICATE_PRICE_TOLERANCE_PCT
            if area_close and price_close:
                duplicate_of[j] = i
    return [duplicate_of.get(i) for i in range(len(lots))]


def build_deduped_lots(lots: list) -> list:
    """Лоты без дублей, со ВСЕМИ исходными полями (включая description, если он есть) —
    analyze()/render_markdown_table() ниже агрегируют по комнатности и текст не сохраняют,
    поэтому pain-point-extractor не может взять его из -table.md (баг найден на реальном
    прогоне ALIA 2026-09-11: market-analyst кладёт description в сырой -comps.json/-target.json
    по своей же инструкции, но до pain-point-extractor он не доживал). Admission-фильтр
    намеренно продублирован из analyze(), не вынесен в общую функцию — чтобы не трогать
    сигнатуру analyze() и не ломать её тесты."""
    duplicate_of = detect_duplicates(lots)
    deduped = []
    for i, lot in enumerate(lots):
        missing = [k for k in ("title", "price", "area", "seller_type", "url") if k not in lot]
        if missing or not lot.get("area") or lot.get("price") is None:
            continue
        if duplicate_of[i] is not None:
            continue
        deduped.append(lot)
    return deduped


def load_snapshot(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("market_snapshot.json должен быть списком объявлений, не объектом/строкой")
    return data


def analyze(lots: list) -> dict:
    by_rooms = defaultdict(list)
    warnings = []
    duplicate_of = detect_duplicates(lots)
    duplicates_removed = sum(1 for d in duplicate_of if d is not None)

    for i, lot in enumerate(lots):
        missing = [k for k in ("title", "price", "area", "seller_type", "url") if k not in lot]
        if missing:
            warnings.append(f"Лот #{i} ({lot.get('title', '?')!r}) — не хватает полей: {missing}, пропущен из расчёта")
            continue
        if not lot["area"] or lot["price"] is None:
            warnings.append(f"Лот #{i} ({lot['title']!r}) — price/area пустые, пропущен")
            continue
        if duplicate_of[i] is not None:
            # Дубль — не считаем отдельным лотом (искажает "сколько реально предложений"),
            # но не выбрасываем молча: суммарный счётчик duplicates_removed идёт в отчёт.
            continue

        # Валидация FORM — присутствует, но вне словаря, это не то же самое, что не указано:
        # первое значит "market-analyst прислал мусор", второе — "поле честно не заполнено".
        seller_present = lot.get("seller_type") is not None
        if seller_present and lot["seller_type"] not in SELLER_TYPE_FORM:
            warnings.append(
                f"Лот #{i} ({lot['title']!r}) — seller_type={lot['seller_type']!r} вне словаря "
                f"{sorted(SELLER_TYPE_FORM)}, похоже на сбой market-analyst, не честный пропуск"
            )
        renovation_present = lot.get("renovation") is not None
        if renovation_present and lot["renovation"] not in RENOVATION_FORM:
            warnings.append(
                f"Лот #{i} ({lot['title']!r}) — renovation={lot['renovation']!r} вне словаря "
                f"{sorted(RENOVATION_FORM)}, похоже на сбой market-analyst, не честный пропуск"
            )

        rooms = lot.get("rooms")
        ppm2 = round(lot["price"] / lot["area"])
        entry = {**lot, "price_per_m2": ppm2}

        # Возраст объявления — учитывается только для вторички (agent/owner), см. docstring.
        seller = lot.get("seller_type")
        age = lot.get("listing_age_days")
        entry["is_stale"] = (
            seller in ("agent", "owner") and age is not None and age >= STALE_LISTING_DAYS_THRESHOLD
        )

        by_rooms[rooms if rooms is not None else "не определено"].append(entry)

    report = {"groups": {}, "warnings": warnings, "duplicates_removed": duplicates_removed}
    for rooms, entries in by_rooms.items():
        dev = [e for e in entries if e.get("seller_type") == "developer"]
        agent = [e for e in entries if e.get("seller_type") == "agent"]
        own = [e for e in entries if e.get("seller_type") == "owner"]
        unknown_seller = [e for e in entries if e.get("seller_type") not in SELLER_TYPE_FORM]
        with_renovation = [e for e in entries if e.get("renovation") == "with"]
        without_renovation = [e for e in entries if e.get("renovation") == "without"]
        stale = [e for e in entries if e.get("is_stale")]

        all_prices = [e["price"] for e in entries if e.get("price") is not None]
        price_min = min(all_prices) if all_prices else None
        price_median = round(statistics.median(all_prices)) if all_prices else None
        price_max = max(all_prices) if all_prices else None

        # Медиана "самих себя" — из реальных лотов собственников этого среза, не из
        # константы эталона. Требует минимум 1 лота с seller_type == "owner" в группе;
        # без них сравнение вторички с вторичкой невозможно, поле остаётся None.
        own_ppm2_list = [e["price_per_m2"] for e in own]
        owner_median_ppm2 = round(statistics.median(own_ppm2_list)) if own_ppm2_list else None

        report["groups"][str(rooms)] = {
            "total_lots": len(entries),
            "developer_lots": len(dev),
            "agent_lots": len(agent),
            "owner_lots": len(own),
            "unknown_seller_lots": len(unknown_seller),
            "price_min": price_min,
            "price_median": price_median,
            "price_max": price_max,
            "with_renovation_lots": len(with_renovation),
            "without_renovation_lots": len(without_renovation),
            "unknown_renovation_lots": len(entries) - len(with_renovation) - len(without_renovation),
            "owner_median_ppm2": owner_median_ppm2,
            "stale_secondary_lots": len(stale),
            "stale_urls": [e["url"] for e in stale],
        }
    return report


def render_markdown_table(report: dict) -> str:
    """Фиксированный табличный вывод — формат утверждён владельцем 2026-09-10, не менять
    порядок/набор колонок без отдельного решения (не результат моей догадки о том, что
    было бы удобно)."""
    header = (
        "| Планировка | Всего лотов | От застройщика | От агента | От собственника | "
        "Цена мин | Цена медиана | Цена макс | С ремонтом | Без ремонта | Не определено (ремонт) | "
        "Устарело (вторичка) |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [header, sep]
    for rooms in sorted(report["groups"].keys(), key=lambda r: (r == "не определено", r)):
        g = report["groups"][rooms]
        label = f"{rooms}-к" if rooms not in ("не определено",) else rooms
        fmt = lambda v: f"{v:,}".replace(",", " ") if isinstance(v, (int, float)) else "—"
        rows.append(
            f"| {label} | {g['total_lots']} | {g['developer_lots']} | {g['agent_lots']} | "
            f"{g['owner_lots']} | {fmt(g['price_min'])} | {fmt(g['price_median'])} | "
            f"{fmt(g['price_max'])} | {g['with_renovation_lots']} | {g['without_renovation_lots']} | "
            f"{g['unknown_renovation_lots']} | {g['stale_secondary_lots']} |"
        )
    if report.get("duplicates_removed"):
        rows.append("")
        rows.append(
            f"_Дублей на вторичке схлопнуто: {report['duplicates_removed']} "
            f"(одна квартира у разных продавцов, не считается отдельным лотом)._"
        )
    return "\n".join(rows)


def main():
    if len(sys.argv) != 2:
        print("Использование: python market_snapshot_analyzer.py <путь до market_snapshot.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    lots = load_snapshot(path)
    report = analyze(lots)
    table_md = render_markdown_table(report)

    out_path = path.parent / f"{path.stem}-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    table_path = path.parent / f"{path.stem}-table.md"
    with open(table_path, "w", encoding="utf-8") as f:
        f.write(table_md + "\n")

    lots_path = path.parent / f"{path.stem}-lots.json"
    deduped_lots = build_deduped_lots(lots)
    with open(lots_path, "w", encoding="utf-8") as f:
        json.dump(deduped_lots, f, ensure_ascii=False, indent=2)

    print(f"Разобрано лотов: {len(lots)}")
    for rooms, g in report["groups"].items():
        median_note = f", медиана собственников: {g['owner_median_ppm2']} ₽/м²" if g['owner_median_ppm2'] is not None else ", медиана собственников: нет данных (owner-лотов 0)"
        print(f"  {rooms}-комн: {g['total_lots']} лотов ({g['developer_lots']} застройщик / {g['agent_lots']} агент / {g['owner_lots']} собственник){median_note}")
    print()
    print(table_md)
    print()
    if report["warnings"]:
        print(f"Предупреждений: {len(report['warnings'])} — см. {out_path}")
    print(f"Полный отчёт: {out_path}")
    print(f"Таблица: {table_path}")
    print(f"Лоты без дублей (с description для pain-point-extractor): {lots_path}")


if __name__ == "__main__":
    main()
