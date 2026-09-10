#!/usr/bin/env python3
"""Детерминированная проверка текста карточки на банк-лист запрещённых слов
(listing-copywriter/SKILL.md) и канцелярит — не полагаться на то, что модель
сама заметит штамп при вычитке. Не заменяет прогон через humanizer/SKILL.md
(снятие ИИ-паттернов по Wikipedia Signs of AI writing) — это отдельная,
детерминированная проверка конкретного, закрытого списка слов/оборотов,
специфичного для рекламных карточек недвижимости на русском.

Использование: python3 check_banned_patterns.py <путь к тексту .txt>
Возвращает JSON: passed (bool), banned_words (список найденных с позицией),
kantselyarit (список найденных канцеляритных оборотов).
"""
from __future__ import annotations

import json
import re
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


# Источник: listing-copywriter/SKILL.md, «Банк-лист запрещённых слов». Многословные
# фразы — на стемах (не точной строкой), иначе склонение слова («инфраструктурой»
# вместо «инфраструктура») тихо проходит проверку — найдено реальным тестом.
BANNED_WORDS = [
    "прекрасн",
    "уникальн",
    "идеальн",
    "не упустите шанс",
    "звоните прямо сейчас",
    r"развит\w*\s+инфраструктур\w*",
    r"динамично\s+развива\w*\s+район\w*",
    r"выгодн\w*\s+услови\w*",
    r"широк\w*\s+возможност\w*",
    r"продуман\w*\s+планировк\w*",
]

# Классический русский канцелярит (Нора Галь, «Слово живое и мёртвое») — обороты,
# которые превращают продающий текст в справку из ЖЭКа, не рекламу для человека.
KANTSELYARIT_PATTERNS = [
    r"в связи с тем[,]? что",
    r"по причине того[,]? что",
    r"в целях\s+\w+",
    r"осуществляется",
    r"производится",
    r"надлежащим образом",
    r"вышеуказанн\w+",
    r"нижеследующ\w+",
    r"следует отметить[,]? что",
    r"необходимо отметить[,]? что",
    r"данн(ый|ая|ое|ые|ого|ой)\s",
    r"является\s+\w+ой\s+частью",
]


def _find_matches(text: str, needles: list[str]) -> list[dict]:
    found = []
    lowered = text.lower()
    for needle in needles:
        for match in re.finditer(needle, lowered):
            found.append({"pattern": needle, "position": match.start(), "context": text[max(0, match.start() - 15):match.end() + 15]})
    return found


def check_text(text: str) -> dict:
    banned = _find_matches(text, BANNED_WORDS)
    kantselyarit = _find_matches(text, KANTSELYARIT_PATTERNS)
    return {
        "passed": not banned and not kantselyarit,
        "banned_words": banned,
        "kantselyarit": kantselyarit,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Использование: python3 {sys.argv[0]} <путь к тексту .txt>")
    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()
    result = check_text(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
