---
name: traffic-calculator
description: Use when Модуль 1 traffic-manager (ставка CPA/CPX) нужно посчитать по реальным цифрам кампании, не оценить на глаз — запускает calculate.py, детерминированный расчёт, не LLM-домысел.
user-invocable: true
allowed-tools: Read, Write, Bash
---

# Traffic Calculator

## Когда нанимают

Модуль 1 субагента `traffic-manager` (`.claude/agents/traffic-manager.md`, ставка CPA/CPX)
получил реальные цифры кампании (текущая ставка, CPA/CPL, целевой порог, период) — вместо
того чтобы прикинуть шаг изменения ставки в голове, посчитать его кодом по формуле, уже
описанной там же.

**Не для Модулей 2 (VAS) и 3 (звонок/жалоба)** — там суждение качественное (готова ли
карточка, похож ли звонок на спам), формула для этого не пишется без выдумывания порогов —
эти два модуля остаются текстовым советом агента, не кодом.

## Как использовать

1. Собрать реальные цифры кампании в JSON (`stats.json`, схема — `calculate.py`, `STATS_SCHEMA`):
   `current_bid`, `current_cpa`, `target_cpa`, `spend_period_days` обязательны;
   `spend_amount`, `ctr`, `position`, `conversions` — опциональны, если есть.
2. `python .claude/skills/traffic-calculator/tools/calculate.py <путь к stats.json>`
3. Скрипт возвращает JSON: `verdict` (короткий вывод), `action`, `bid_change_percent`,
   `new_bid`, `reasoning` (список причин расчёта — не чёрный ящик), `confidence`.
4. Взять `verdict`/`reasoning` из ответа скрипта и оформить по Output Contract `CLAUDE.md` (§1)
   (строка 1 — вердикт+действие, дальше 3-4 компактных пункта) — скрипт не заменяет формат
   ответа владельцу, только считает число.

## Гарантия read-only

Файл физически не импортирует `requests`/`urllib`/`http.client`/`socket`/`httpx` и в начале
проверяет через `assert`, что эти модули не загружены ни в одну из зависимостей — при
случайном появлении сетевого импорта в будущем скрипт упадёт явной ошибкой при старте, не
получит тихий доступ к Avito API. Это техническая защита advisor-only контракта
(`CLAUDE.md`, §2/§7), не только текстовый запрет.

## Обратная связь

Как и для остальных модулей — результат в `workspace/traffic-recommendations-log.md`
(`.claude/agents/traffic-manager.md`, «Обратная связь»), не отдельный лог только для этого скилла.
