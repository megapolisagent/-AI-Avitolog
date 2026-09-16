#!/bin/bash
# Технический гейт на реально мутирующие вызовы Avito API — деньги и публичные
# действия, не просто субагент. avito_api_client.py сам помечает 7 методов
# комментарием "МУТИРУЮЩИЙ" (найдено чтением файла целиком, 2026-09-14):
#   answer_review, delete_review_answer   — публичный ответ на отзыв (необратимо видно всем)
#   create_offers, confirm_offers         — спецпредложение реально уходит покупателю
#   create_bbip_order                     — тратит деньги с Кошелька Avito
#   apply_trxpromo, cancel_trxpromo       — включает/выключает платное продвижение за комиссию
#
# CLAUDE.md §3/§9 уже требует "только через явное да владелицы, каждый раз заново" для
# этого класса действий — текстом. До этого хука ничего не проверяло это технически:
# модель могла решить, что конкретный вызов "достаточно обоснован", и просто выполнить
# его. Разница с block-dangerous-git.sh: там действие бессмысленно почти всегда и
# блокируется жёстко; здесь действие — штатная часть работы агента, просто требует
# подтверждения каждый раз — поэтому ответ не "deny", а "ask": харнесс сам покажет
# owner реальный permission-prompt в момент вызова, агент не может тихо на него ответить.
#
# Матчится на Bash/PowerShell (вызов инлайн через python3 -c / временный скрипт) И на
# Write/Edit (сам метод только что записан в файл скрипта, который дальше запустят) —
# оба пути реальны, ловить нужно оба, иначе гейт обходится тривиально написанием
# скрипта в отдельном шаге.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

MUTATING_RE='\.(answer_review|delete_review_answer|create_offers|confirm_offers|create_bbip_order|apply_trxpromo|cancel_trxpromo)[[:space:]]*\('

case "$TOOL" in
  Bash|PowerShell)
    TEXT=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    ;;
  Write|Edit)
    TEXT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')
    ;;
  *)
    exit 0
    ;;
esac

if echo "$TEXT" | grep -qE "$MUTATING_RE"; then
  MATCHED=$(echo "$TEXT" | grep -oE "$MUTATING_RE" | head -1 | tr -d '.(' | tr -d '[:space:]')
  REASON="Обнаружен вызов мутирующего метода Avito API ($MATCHED) — тратит деньги или публикует что-то необратимо (CLAUDE.md §3/§9: только через явное да владелицы, каждый раз заново). Подтвердите, что владелица уже сказала явное да именно на это конкретное действие, прежде чем разрешать."
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$REASON"
  exit 0
fi

exit 0
