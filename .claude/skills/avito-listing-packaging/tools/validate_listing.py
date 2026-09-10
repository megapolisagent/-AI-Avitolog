#!/usr/bin/env python3
"""Финальный валидатор карточки (SKILL.md, «Финальный валидатор перед выдачей карточки»),
переведён из прозы в код по находке независимого аудита 2026-09-10: это закрытый чек-лист
по фиксированным правилам площадки, а не суждение — ровно тот же класс риска, что уже
закрывался для платежа по ипотеке/рассрочке (listing-fin-offer/tools/calculate_payment.py) и
для банк-листа слов (humanizer/tools/check_banned_patterns.py).

Правила 1-3 проверяются точно (детерминированный факт против входных данных). Правило 4
(«не заявлять цену/условие, которых нет во входных данных») в общем виде требует понимания
смысла текста — здесь это честно best-effort: числа-с-процентом в тексте карточки сверяются
со списком подтверждённых чисел из данных лота, несовпадение — не автоматический провал, а
`needs_review` для человека/агента, который писал текст.

Использование: python3 validate_listing.py <путь к JSON со входом>, схема — INPUT_SCHEMA ниже.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

INPUT_SCHEMA = {
    "price_field_value": "число — то, что уходит в поле «Цена» на Avito",
    "full_price": "число — подтверждённая полная цена лота из data/lots (обязательна)",
    "listing_text": "строка — текст карточки/описания для проверки ссылок/телефона",
    "floor_input": "число или null — этаж из входных данных лота; null = [НЕТ ДАННЫХ]",
    "floor_in_text": "число или null — этаж, упомянутый в тексте карточки, если есть",
    "confirmed_percent_facts": "список чисел, опционально — все проценты, реально данные во входных "
                                "данных лота (скидка, ставка, ПВ %) — база для сверки правила 4",
}

PHONE_RE = re.compile(r"(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
LINK_RE = re.compile(
    r"(https?://|www\.|\bvk\.com\b|\bt\.me\b|\binstagram\.[a-z]+\b|\bwa\.me\b|\bwhatsapp\b)",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


class InputError(ValueError):
    pass


def _require(data: dict, field: str):
    if field not in data or data[field] is None:
        raise InputError(f"Обязательное поле «{field}» не задано.")
    return data[field]


def validate_listing(data: dict) -> dict:
    price_field_value = _require(data, "price_field_value")
    full_price = _require(data, "full_price")
    listing_text = _require(data, "listing_text")
    floor_input = data.get("floor_input")
    floor_in_text = data.get("floor_in_text")
    confirmed_percent_facts = data.get("confirmed_percent_facts", [])

    checks = []

    # Правило 1 — в поле «Цена» только полная стоимость, не ПВ/платёж.
    if price_field_value == full_price:
        checks.append({"rule": "price_field_is_full_price", "status": "pass",
                        "detail": f"Поле «Цена» ({price_field_value} ₽) совпадает с полной ценой лота."})
    else:
        checks.append({"rule": "price_field_is_full_price", "status": "fail",
                        "detail": f"Поле «Цена» ({price_field_value} ₽) не равно полной цене лота "
                                  f"({full_price} ₽) — похоже на ПВ/платёж вместо полной стоимости."})

    # Правило 2 — нет телефона и внешних ссылок/соцсетей в тексте.
    phone_hits = PHONE_RE.findall(listing_text)
    link_hits = LINK_RE.findall(listing_text)
    if not phone_hits and not link_hits:
        checks.append({"rule": "no_phone_or_external_links", "status": "pass", "detail": "Не найдено."})
    else:
        checks.append({"rule": "no_phone_or_external_links", "status": "fail",
                        "detail": f"Найдено: телефон={bool(phone_hits)}, ссылка/соцсеть={bool(link_hits)}."})

    # Правило 3 — этаж в тексте совпадает со входными данными, либо не заявлен, если данных нет.
    if floor_input is None:
        if floor_in_text is None:
            checks.append({"rule": "floor_matches_input", "status": "pass",
                            "detail": "Этаж не задан во входных данных и не заявлен в тексте — верно."})
        else:
            checks.append({"rule": "floor_matches_input", "status": "fail",
                            "detail": f"В тексте заявлен этаж {floor_in_text}, хотя во входных данных "
                                      "этажа нет — выдуманное значение."})
    else:
        if floor_in_text is None or floor_in_text == floor_input:
            checks.append({"rule": "floor_matches_input", "status": "pass",
                            "detail": f"Этаж {floor_input} из входных данных не искажён в тексте."})
        else:
            checks.append({"rule": "floor_matches_input", "status": "fail",
                            "detail": f"В тексте этаж {floor_in_text}, во входных данных — {floor_input}."})

    # Правило 4 — best-effort сверка процентных утверждений с подтверждёнными фактами.
    text_percents = [float(p.replace(",", ".")) for p in PERCENT_RE.findall(listing_text)]
    confirmed = {round(float(p), 2) for p in confirmed_percent_facts}
    unmatched = [p for p in text_percents if round(p, 2) not in confirmed]
    if not text_percents:
        checks.append({"rule": "percent_claims_backed_by_input", "status": "pass",
                        "detail": "В тексте нет процентных утверждений для сверки."})
    elif not unmatched:
        checks.append({"rule": "percent_claims_backed_by_input", "status": "pass",
                        "detail": f"Все процентные утверждения ({text_percents}) есть во входных данных."})
    else:
        checks.append({"rule": "percent_claims_backed_by_input", "status": "needs_review",
                        "detail": f"В тексте проценты {unmatched}, не найденные в подтверждённых "
                                  "фактах лота — не автоматический провал (правило не понимает "
                                  "смысл фразы), но требует ручной сверки перед публикацией."})

    hard_fails = [c for c in checks if c["status"] == "fail"]
    needs_review = [c for c in checks if c["status"] == "needs_review"]

    if hard_fails:
        verdict = f"не готово к публикации — {len(hard_fails)} нарушение(й) из чек-листа Avito"
    elif needs_review:
        verdict = f"готово, кроме {len(needs_review)} пункта(ов) на ручную проверку"
    else:
        verdict = "готово к публикации — все пункты чек-листа пройдены"

    return {"verdict": verdict, "passed": not hard_fails, "checks": checks}


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: python3 validate_listing.py <путь к JSON со входом>", file=sys.stderr)
        sys.exit(2)

    try:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать вход: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = validate_listing(data)
    except InputError as exc:
        print(f"Недостаточно данных: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
