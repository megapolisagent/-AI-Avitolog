#!/usr/bin/env python3
"""Реальный расчётный код для Модуля 1 (`.claude/agents/traffic-manager.md`) — рекомендация
по ставке CPA/CPX. Читает только локальный JSON со статистикой конкретной кампании (владелец/
Avitolog его дал), не делает никаких сетевых вызовов и физически не может вызвать Avito API:
никакого `requests`/`urllib`/`http.client`/`socket` в коде и в зависимостях этого файла.

Модули 2 (VAS-продвижение) и 3 (разбор звонка) остаются текстовым суждением субагента —
они завязаны на качественную оценку (готова ли карточка, похож ли звонок на спам), которую
нельзя честно свести к формуле без выдумывания порогов. Здесь считается только то, что уже
описано как числовая логика: шаг 5-30% от расхождения CPA/CPL с целевым порогом, порог
пересмотра ~10-15%.

Использование: python calculate.py <путь к stats.json>
Формат stats.json — см. STATS_SCHEMA ниже.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Жёсткая защита read-only: если что-то в будущем импортирует сетевой модуль — упасть
# явной ошибкой при старте, не позволить скрипту тихо получить сетевой доступ.
_FORBIDDEN_NETWORK_MODULES = ("requests", "urllib.request", "http.client", "socket", "httpx")
for _mod in _FORBIDDEN_NETWORK_MODULES:
    assert _mod not in sys.modules, (
        f"Модуль {_mod} уже загружен — traffic-calculator обязан оставаться read-only, "
        "без сетевого доступа. Это нарушение architecture-контракта (advisor-only), не мелочь."
    )

STATS_SCHEMA = {
    "current_bid": "число — текущая ставка, ₽",
    "current_cpa": "число — текущий CPA/CPL за период (цена за лид)",
    "target_cpa": "число — целевой порог CPA/CPL, обязателен, не выдумывается",
    "spend_period_days": "число — за сколько дней считаны current_cpa/spend_amount (нужно 3+ для делового сигнала)",
    "spend_amount": "число, опционально — расход за период, ₽",
    "ctr": "число (0-100), опционально — CTR, %",
    "position": "число, опционально — позиция в аукционе (меньше = выше)",
    "conversions": "целое, опционально — число реальных лидов за период",
}

# Пороги — только из config.json (pipeline-architecture.md §4), не хардкод. Найдено реальным
# аудитом 2026-09-10: значения были зашиты прямо в код, хотя тот же принцип уже применялся
# к порогам market_snapshot_analyzer.py — перенесено, сами значения не менялись.
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "config.json"
with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _TM_CONFIG = json.load(_f)["traffic_manager"]

REVIEW_THRESHOLD_PCT = _TM_CONFIG["review_threshold_pct"]
MIN_STEP_PCT = _TM_CONFIG["min_step_pct"]
MAX_STEP_PCT = _TM_CONFIG["max_step_pct"]


class InputError(ValueError):
    pass


def _require(stats: dict, field: str):
    if field not in stats or stats[field] is None:
        raise InputError(
            f"Обязательное поле «{field}» не задано в stats.json — не считаю на предположении, "
            "как того требует SKILL.md («Без реальных цифр — не считаешь»)."
        )
    return stats[field]


def calculate_bid_recommendation(stats: dict) -> dict:
    current_bid = _require(stats, "current_bid")
    current_cpa = _require(stats, "current_cpa")
    target_cpa = _require(stats, "target_cpa")
    spend_period_days = _require(stats, "spend_period_days")

    if target_cpa <= 0:
        raise InputError("target_cpa должен быть положительным числом.")

    deviation_pct = (current_cpa - target_cpa) / target_cpa * 100

    reasoning = [
        f"CPA/CPL за период: {current_cpa} ₽, целевой порог: {target_cpa} ₽ "
        f"(расхождение {deviation_pct:+.1f}%).",
    ]

    if spend_period_days < 3:
        reasoning.append(
            f"Период наблюдения — {spend_period_days} дн., меньше рекомендованных 3+ дней "
            "для делового сигнала по CPA (traffic-manager.md, «отложенный сигнал»)."
        )
        return {
            "verdict": "недостаточно данных для решения по ставке",
            "action": "no_change",
            "bid_change_percent": 0,
            "new_bid": current_bid,
            "reasoning": reasoning,
            "confidence": "low",
        }

    if abs(deviation_pct) < REVIEW_THRESHOLD_PCT:
        reasoning.append(
            f"Расхождение внутри порога пересмотра (±{REVIEW_THRESHOLD_PCT:.0f}%) — "
            "это шум периода, не повод менять ставку."
        )
        return {
            "verdict": "ставку не менять",
            "action": "no_change",
            "bid_change_percent": 0,
            "new_bid": current_bid,
            "reasoning": reasoning,
            "confidence": "medium",
        }

    # Шаг пропорционален величине расхождения, но зажат в 5..30%.
    step_pct = max(MIN_STEP_PCT, min(MAX_STEP_PCT, round(abs(deviation_pct) / 2)))

    if deviation_pct > 0:
        # CPA выше порога — дороже, чем нужно.
        spend_amount = stats.get("spend_amount")
        conversions = stats.get("conversions")
        big_spend_no_result = (
            spend_amount is not None
            and conversions is not None
            and conversions == 0
            and spend_amount > 0
        )
        if big_spend_no_result:
            reasoning.append(
                f"Расход {spend_amount} ₽ за период дал 0 реальных лидов — SKILL.md прямо "
                "предупреждает: «не любое превышение решается деньгами», проблема вероятно "
                "в самой карточке, не в ставке."
            )
            return {
                "verdict": "не повышать ставку — разобраться в карточке/лоте",
                "action": "review_listing",
                "bid_change_percent": 0,
                "new_bid": current_bid,
                "reasoning": reasoning,
                "confidence": "medium",
            }

        new_bid = round(current_bid * (1 + step_pct / 100), 2)
        reasoning.append(
            f"CPA выше целевого порога — рекомендация повысить ставку на {step_pct}% "
            f"({current_bid} ₽ → {new_bid} ₽) для улучшения позиции."
        )
        return {
            "verdict": f"повысить ставку до {new_bid} ₽",
            "action": "increase_bid",
            "bid_change_percent": step_pct,
            "new_bid": new_bid,
            "reasoning": reasoning,
            "confidence": "medium",
        }
    else:
        new_bid = round(current_bid * (1 - step_pct / 100), 2)
        reasoning.append(
            f"CPA ниже целевого порога — есть запас, можно осторожно снизить ставку на "
            f"{step_pct}% ({current_bid} ₽ → {new_bid} ₽) и понаблюдать, не упадёт ли конверсия."
        )
        return {
            "verdict": f"осторожно снизить ставку до {new_bid} ₽, понаблюдать",
            "action": "decrease_bid",
            "bid_change_percent": -step_pct,
            "new_bid": new_bid,
            "reasoning": reasoning,
            "confidence": "medium",
        }


def main() -> None:
    # Консоль Windows по умолчанию — cp1251, не кодирует ₽ и другие юникод-символы вне
    # этой кодовой страницы. Найдено реальным тестовым прогоном 2026-09-03 (UnicodeEncodeError
    # на первом же запуске) — не гипотетический риск.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: python calculate.py <путь к stats.json>", file=sys.stderr)
        sys.exit(2)

    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            stats = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать stats.json: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = calculate_bid_recommendation(stats)
    except InputError as exc:
        print(f"Недостаточно данных: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
