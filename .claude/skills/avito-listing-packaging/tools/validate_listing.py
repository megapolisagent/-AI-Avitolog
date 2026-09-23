#!/usr/bin/env python3
"""Финальный валидатор карточки (SKILL.md, «Финальный валидатор перед выдачей карточки»),
переведён из прозы в код по находке независимого аудита 2026-09-10: это закрытый чек-лист
по фиксированным правилам площадки, а не суждение — ровно тот же класс риска, что уже
закрывался для платежа по ипотеке/рассрочке (listing-fin-offer/tools/calculate_payment.py) и
для банк-листа слов (listing-copywriter/tools/check_banned_patterns.py).

Правила 1-3 проверяются точно (детерминированный факт против входных данных). Правило 4
(«не заявлять цену/условие, которых нет во входных данных») в общем виде требует понимания
смысла текста — здесь это честно best-effort: числа-с-процентом в тексте карточки сверяются
со списком подтверждённых чисел из данных лота, несовпадение — не автоматический провал, а
`needs_review` для человека/агента, который писал текст.

Правила 5-6 добавлены 2026-09-17 по ТЗ CMO (закрытие разрыва факт/регламент,
`references/avito-operating-playbook.md`, раздел «Привязка к пайплайну» — пункты 1 и 2,
приоритет «деньги/штрафы»). Правило 5 — гейт группы допуска застройщика (playbook, раздел 3):
Группа 3 не публикуется никогда, Группа 2 — только с письменным акцептом. Правило 6 —
обязательный юридический блок карточки (playbook, раздел 5) для объектов первичного рынка:
ссылка на ДДУ/214-ФЗ, юр. лицо застройщика в тексте, ссылка на наш.дом.рф. Статус партнёрства
(«уполномоченный»/«официальный») из раздела 5 сюда сознательно не входит — открытый вопрос
владелицы, не решается этим скриптом (`avito-operating-playbook.md`, раздел 5, `[УТОЧНИТЬ]`).

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
    "developer_group": "1, 2, 3 или null — группа допуска застройщика (playbook, раздел 3/6); "
                        "null = лот не от застройщика (вторичка), правило 5 неприменимо",
    "developer_acceptance_confirmed": "bool, опционально — только для Группы 2: получен ли "
                                       "письменный акцепт застройщика на этот конкретный лот",
    "is_primary_market": "bool — лот от застройщика (первичка, ДДУ)? Определяет, применяется ли "
                          "правило 6 (юрблок обязателен только для первички, playbook раздел 5)",
    "developer_legal_entity": "строка или null — точное юр. лицо застройщика из проектной "
                               "декларации (наш.дом.рф), обязательно для правила 6, если "
                               "is_primary_market",
}

PHONE_RE = re.compile(r"(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
LINK_RE = re.compile(
    r"(https?://|www\.|\bvk\.com\b|\bt\.me\b|\binstagram\.[a-z]+\b|\bwa\.me\b|\bwhatsapp\b)",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
DDU_RE = re.compile(r"(ДДУ|214-ФЗ|№\s*214-ФЗ|долевого строительства)", re.IGNORECASE)
NASHDOM_RE = re.compile(r"наш\.дом\.рф", re.IGNORECASE)
VALID_DEVELOPER_GROUPS = {1, 2, 3}


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
    developer_group = data.get("developer_group")
    developer_acceptance_confirmed = data.get("developer_acceptance_confirmed", False)
    is_primary_market = data.get("is_primary_market", False)
    developer_legal_entity = data.get("developer_legal_entity")

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

    # Правило 5 — гейт группы допуска застройщика (playbook, раздел 3/6). Не лот от застройщика
    # (developer_group is None) — правило неприменимо, не блокирует вторичку.
    if developer_group is None:
        checks.append({"rule": "developer_group_gate", "status": "pass",
                        "detail": "developer_group не задан — лот не от застройщика, правило неприменимо."})
    elif developer_group not in VALID_DEVELOPER_GROUPS:
        checks.append({"rule": "developer_group_gate", "status": "fail",
                        "detail": f"developer_group={developer_group!r} вне допустимых значений "
                                  f"{sorted(VALID_DEVELOPER_GROUPS)} — похоже на ошибку данных, "
                                  "не публикуется без исправления."})
    elif developer_group == 3:
        checks.append({"rule": "developer_group_gate", "status": "fail",
                        "detail": "Группа 3 — прямой запрет на классифайды (playbook, раздел 3). "
                                  "Публикация недопустима ни при каких условиях."})
    elif developer_group == 2 and not developer_acceptance_confirmed:
        checks.append({"rule": "developer_group_gate", "status": "fail",
                        "detail": "Группа 2 требует письменного акцепта застройщика на этот лот "
                                  "(playbook, раздел 4/8) — developer_acceptance_confirmed не подтверждён."})
    else:
        checks.append({"rule": "developer_group_gate", "status": "pass",
                        "detail": f"Группа {developer_group} — публикация разрешена "
                                  f"({'акцепт получен' if developer_group == 2 else 'без согласования'})."})

    # Правило 6 — обязательный юридический блок карточки (playbook, раздел 5), только для первички.
    # Статус партнёрства («уполномоченный»/«официальный») сознательно не проверяется — открытый
    # вопрос владелицы (playbook, раздел 5, [УТОЧНИТЬ]), не решается этим скриптом.
    if not is_primary_market:
        checks.append({"rule": "legal_block_present", "status": "pass",
                        "detail": "is_primary_market=False — лот не первичка, юрблок не обязателен."})
    elif not developer_legal_entity:
        checks.append({"rule": "legal_block_present", "status": "fail",
                        "detail": "Первичка без юр. лица застройщика (developer_legal_entity) — "
                                  "юрблок не может быть составлен, публикация заблокирована."})
    else:
        missing = []
        if developer_legal_entity not in listing_text:
            missing.append("юр. лицо застройщика")
        if not DDU_RE.search(listing_text):
            missing.append("ссылка на ДДУ/214-ФЗ")
        if not NASHDOM_RE.search(listing_text):
            missing.append("ссылка на наш.дом.рф")
        if missing:
            checks.append({"rule": "legal_block_present", "status": "fail",
                            "detail": f"В тексте карточки не найдено: {', '.join(missing)} "
                                      "(playbook, раздел 5 — обязательный юридический блок)."})
        else:
            checks.append({"rule": "legal_block_present", "status": "pass",
                            "detail": "Юрлицо застройщика, ссылка на ДДУ/214-ФЗ и наш.дом.рф найдены в тексте."})

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
