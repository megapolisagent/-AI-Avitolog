#!/bin/bash
# Блокирует необратимые/деструктивные локальные git-операции независимо от того,
# через какой инструмент пришла команда (Bash или PowerShell) — settings.json
# регистрирует этот же скрипт на оба matcher'а, один список паттернов, не два.
# Force-push остаётся заблокирован; обычный `git push` — нет.
#
# Перенесено из ENGINEER 2026-09-14 — тот же барьер, применимый к любому агенту
# с неограниченным Bash, не специфика инженерной сборки.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')
# Схлопывает любые повторы пробелов/табов/переносов строк в один пробел —
# закрывает обход вида "git   reset    --hard".
NORMALIZED=$(echo "$COMMAND" | tr -s '[:space:]' ' ')

# Регистронезависимые паттерны. Сам git всё равно не примет команду в другом
# регистре как валидную ("git RESET" — 'RESET' is not a git command) — эта
# нечувствительность чистая паранойя на случай нестандартных обёрток/алиасов,
# без риска ложно заблокировать легитимную команду.
CI_PATTERNS=(
  "git[[:space:]]+reset[[:space:]]+--hard"
  "git[[:space:]]+clean[[:space:]]+-fd"
  "git[[:space:]]+clean[[:space:]]+-f([[:space:]]|$)"
  "git[[:space:]]+checkout[[:space:]]+\."
  "git[[:space:]]+restore[[:space:]]+\."
  "push[[:space:]]+--force"
  "push[[:space:]]+-f([[:space:]]|$)"
)

for pattern in "${CI_PATTERNS[@]}"; do
  if echo "$NORMALIZED" | grep -qiE "$pattern"; then
    echo "BLOCKED: '$COMMAND' matches dangerous pattern '$pattern'. The user has prevented you from doing this." >&2
    exit 2
  fi
done

# Регистрозависимая проверка, намеренно НЕ regex-insensitive: `-D` (force-delete,
# может потерять неслитые коммиты) и `-d` (safe delete, только если ветка слита) —
# разная семантика в реальном git. Регистронезависимое совпадение здесь заблокировало
# бы безопасную повседневную операцию `git branch -d`, а не только опасную `-D`.
if echo "$NORMALIZED" | grep -qE "git[[:space:]]+branch[[:space:]]+-D"; then
  echo "BLOCKED: '$COMMAND' matches dangerous pattern 'git branch -D'. The user has prevented you from doing this." >&2
  exit 2
fi

exit 0
