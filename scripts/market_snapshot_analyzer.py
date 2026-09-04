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
    "seller_type": "developer" | "owner",
    "url": str
  }

Что делает:
  1. Группирует лоты по rooms.
  2. Считает цену за м² для каждого лота.
  3. Медиану по группе — из лотов seller_type == "owner" (сравнение "самих себя" вторички
     с самой собой), затем сверяет с эталоном ASTERUS (knowledge/alia-asterus-price-benchmark.md,
     жёстко прописан ниже, единственный источник эталона на сегодня).
  4. Отсекает как "подозрительно занижено" всё, что ниже эталонного порога по комнатности
     более чем на 15% (порог владельца, `workspace/2026-09-01-задача-*.md` про "фонари") —
     не удаляет из отчёта, помечает явно, решение по каждому — за человеком.
  5. Выводит рекомендованный коридор публикации: [эталон_мин * 0.85, эталон_макс] —
     нижняя граница — порог "не фонарь", верхняя — не завышать против рынка бессмысленно.

Честно: скрипт не проверяет, откуда взялся market_snapshot.json, не умеет отличить
настоящее объявление от подставного номера в самом файле — это ответственность
того, кто наполняет файл, не этого скрипта.
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Эталон ASTERUS по комнатности — из knowledge/alia-asterus-price-benchmark.md, 2026-09-01.
# Дублируется здесь как константа, не читается из markdown на лету (нет парсера markdown-таблиц
# в проекте) — если эталон обновится, поправить оба места, это отмечено тут и там.
ASTERUS_BENCHMARK_PPM2 = {
    1: {"min": 527100, "median": 631850, "max": 747650},
    2: {"min": 500500, "median": 623000, "max": 664510},
    3: {"min": 595980, "median": 696000, "max": 741000},
}
FAKE_THRESHOLD_PCT = 0.15  # порог владельца — 15% ниже эталона без подтверждения


def load_snapshot(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("market_snapshot.json должен быть списком объявлений, не объектом/строкой")
    return data


def analyze(lots: list) -> dict:
    by_rooms = defaultdict(list)
    warnings = []

    for i, lot in enumerate(lots):
        missing = [k for k in ("title", "price", "area", "seller_type", "url") if k not in lot]
        if missing:
            warnings.append(f"Лот #{i} ({lot.get('title', '?')!r}) — не хватает полей: {missing}, пропущен из расчёта")
            continue
        if not lot["area"] or lot["price"] is None:
            warnings.append(f"Лот #{i} ({lot['title']!r}) — price/area пустые, пропущен")
            continue

        rooms = lot.get("rooms")
        ppm2 = round(lot["price"] / lot["area"])
        entry = {**lot, "price_per_m2": ppm2}

        bench = ASTERUS_BENCHMARK_PPM2.get(rooms)
        if bench:
            floor = bench["min"] * (1 - FAKE_THRESHOLD_PCT)
            entry["below_15pct_of_benchmark_min"] = ppm2 < floor
            entry["vs_median_pct"] = round((ppm2 - bench["median"]) / bench["median"] * 100, 1)
        else:
            entry["below_15pct_of_benchmark_min"] = None
            entry["vs_median_pct"] = None
            warnings.append(f"Лот #{i} ({lot['title']!r}) — комнатность {rooms!r} без эталона ASTERUS, пометка пропущена")

        by_rooms[rooms if rooms in ASTERUS_BENCHMARK_PPM2 else "не определено"].append(entry)

    report = {"groups": {}, "warnings": warnings}
    for rooms, entries in by_rooms.items():
        dev = [e for e in entries if e.get("seller_type") == "developer"]
        own = [e for e in entries if e.get("seller_type") == "owner"]
        suspicious = [e for e in entries if e.get("below_15pct_of_benchmark_min")]
        bench = ASTERUS_BENCHMARK_PPM2.get(rooms)

        # Медиана "самих себя" — из реальных лотов собственников этого среза, не из
        # константы эталона. Требует минимум 1 лота с seller_type == "owner" в группе;
        # без них сравнение вторички с вторичкой невозможно, поле остаётся None.
        own_ppm2_list = [e["price_per_m2"] for e in own]
        owner_median_ppm2 = round(statistics.median(own_ppm2_list)) if own_ppm2_list else None
        owner_median_vs_asterus_pct = (
            round((owner_median_ppm2 - bench["median"]) / bench["median"] * 100, 1)
            if (owner_median_ppm2 is not None and bench) else None
        )

        recommended_corridor = None
        if bench:
            recommended_corridor = {
                "min_ppm2": round(bench["min"] * (1 - FAKE_THRESHOLD_PCT)),
                "max_ppm2": bench["max"],
                "note": "нижняя граница = эталон-минимум минус 15% (порог 'не фонарь'), верхняя = эталон-максимум",
            }

        report["groups"][str(rooms)] = {
            "total_lots": len(entries),
            "developer_lots": len(dev),
            "owner_lots": len(own),
            "owner_median_ppm2": owner_median_ppm2,
            "owner_median_vs_asterus_pct": owner_median_vs_asterus_pct,
            "suspicious_underpriced": len(suspicious),
            "suspicious_urls": [e["url"] for e in suspicious],
            "asterus_benchmark_ppm2": bench,
            "recommended_publish_corridor_ppm2": recommended_corridor,
        }
    return report


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

    out_path = path.parent / f"{path.stem}-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Разобрано лотов: {len(lots)}")
    for rooms, g in report["groups"].items():
        median_note = f", медиана собственников: {g['owner_median_ppm2']} ₽/м²" if g['owner_median_ppm2'] is not None else ", медиана собственников: нет данных (owner-лотов 0)"
        print(f"  {rooms}-комн: {g['total_lots']} лотов ({g['developer_lots']} застройщик / {g['owner_lots']} собственник), подозрительно занижено: {g['suspicious_underpriced']}{median_note}")
    if report["warnings"]:
        print(f"Предупреждений: {len(report['warnings'])} — см. {out_path}")
    print(f"Полный отчёт: {out_path}")


if __name__ == "__main__":
    main()
