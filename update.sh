#!/usr/bin/env bash
# Безопасное обновление Foundation.
#
# Что делает: скачивает свежую версию служебной части (скиллы, устав, инструкции)
# и обновляет ТОЛЬКО её.
#
# Чего не делает НИКОГДА: не трогает личные файлы —
#   PROFILE.md, SOUL.md, MEMORY.md, memory/, knowledge/, workspace/
#
# Запуск: bash update.sh
#
# Тело обёрнуто в функцию main специально: так bash читает файл целиком ДО выполнения,
# и скрипт может безопасно обновить сам себя в конце работы.

set -euo pipefail

main() {
  SKLAD="${FOUNDATION_SOURCE_URL:-https://github.com/megapolisagent/FOUNDATION.git}"
  TMP=".obnovlenie-tmp"

  # Папки, которые обновляются целиком.
  # "skills" сюда сознательно не входит: Foundation отгружает внутрь неё только installer,
  # а он — Skill, который специализация вправе донастраивать (см. DECISIONS.md, 2026-08-12).
  # Слепое cp -R стёрло бы такую донастройку молча.
  SLUZHEBNYE_PAPKI=("instructions")

  # Служебные файлы, которые обновляются целиком.
  # HOME.md, AGENTS.md, ROUTING.md сюда сознательно не входят: в каждом из них
  # часть содержимого — точка расширения специализации (роль, таблица Skills,
  # добавленные строки роутинга), а не только служебный каркас Foundation.
  # Перезапись файла целиком стирала бы это молча — найдено и исправлено 2026-08-12,
  # подтверждено реальным прогоном на копии (workspace/2026-08-12-update-sh-fix-evidence.md).
  # update.sh тоже исключён из само-обновления: пока апстрим-версия Foundation
  # не несёт того же исправления, самообновление вернуло бы старый (небезопасный)
  # список при следующем запуске и свело бы эту правку на нет.
  # .gitignore добавлен 2026-08-26: чистый служебный файл без точек расширения
  # специализации, безопасен для целиком-перезаписи — но только с маркером
  # "## Локальная адаптация" (тот же цикл ниже, что и для SLUZHEBNYE_PAPKI).
  SLUZHEBNYE_FAYLY=(".gitignore")

  # Личные файлы — список для проверки, что мы их не задели.
  LICHNYE=("PROFILE.md" "SOUL.md" "MEMORY.md" "memory" "knowledge" "workspace")

  echo "Обновляю Foundation. Личные файлы не трогаю."
  echo

  if [ ! -f "HOME.md" ]; then
    echo "Не вижу файла HOME.md — похоже, это не корень дома."
    echo "Перейди в папку дома и запусти скрипт оттуда."
    exit 1
  fi

  SLEPOK_DO="$(sled_lichnyh "${LICHNYE[@]}")"

  if [ -e "$TMP" ]; then
    echo "Папка $TMP уже существует. Удали её вручную и запусти снова."
    exit 1
  fi

  echo "Скачиваю свежую версию из репозитория-источника..."
  git clone --quiet --depth 1 "$SKLAD" "$TMP"

  echo "Обновляю служебную часть:"

  # Маркер локальной адаптации внутри отдельного служебного файла — та же
  # конвенция, что уже понимает `skillctl.py compare` в Skill System: всё
  # до строки "## Локальная адаптация" — канон, всё после — свой, не
  # сверяемый слой. Найдено и исправлено 2026-08-18: cp -R целиком стирал
  # такой слой молча (реальный случай — instructions/pre-task-check.md
  # у Researcher).
  MARKER="## Локальная адаптация"

  for papka in "${SLUZHEBNYE_PAPKI[@]}"; do
    if [ -d "$TMP/$papka" ]; then
      mkdir -p "$papka"
      while IFS= read -r -d '' src; do
        rel="${src#"$TMP/"}"
        mkdir -p "$(dirname "$rel")"
        if [ -f "$rel" ] && grep -qxF "$MARKER" "$rel"; then
          khvost="$(awk -v m="$MARKER" 'BEGIN{p=0} $0==m{p=1} p' "$rel")"
          cp "$src" "$rel"
          printf '\n%s\n' "$khvost" >> "$rel"
        else
          cp "$src" "$rel"
        fi
      done < <(find "$TMP/$papka" -type f -print0)
      echo "   $papka (локальная адаптация внутри файлов сохранена, если была)"
    fi
  done

  for fayl in "${SLUZHEBNYE_FAYLY[@]}"; do
    if [ -f "$TMP/$fayl" ]; then
      mkdir -p "$(dirname "$fayl")"
      if [ -f "$fayl" ] && grep -qxF "$MARKER" "$fayl"; then
        khvost="$(awk -v m="$MARKER" 'BEGIN{p=0} $0==m{p=1} p' "$fayl")"
        cp "$TMP/$fayl" "$fayl"
        printf '\n%s\n' "$khvost" >> "$fayl"
      else
        cp "$TMP/$fayl" "$fayl"
      fi
      echo "   $fayl (локальная адаптация сохранена, если была)"
    fi
  done

  rm -rf "$TMP"

  SLEPOK_POSLE="$(sled_lichnyh "${LICHNYE[@]}")"

  echo
  if [ "$SLEPOK_DO" = "$SLEPOK_POSLE" ]; then
    echo "Проверка пройдена: личные файлы не изменились."
  else
    echo "ВНИМАНИЕ: личные файлы изменились — такого быть не должно."
    echo "Ничего не удаляй и сообщи об этом до того, как продолжишь."
    exit 1
  fi

  echo
  echo "Готово. Закрой этот чат и открой новый — агент подхватит обновления."
}

# Слепок содержимого личных файлов: список «контрольная сумма - файл».
sled_lichnyh() {
  for p in "$@"; do
    if [ -e "$p" ]; then
      find "$p" -type f -exec shasum {} \; 2>/dev/null | sort
    fi
  done
}

main "$@"
