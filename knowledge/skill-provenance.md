# Skill Provenance

Источник, версия донора на момент копирования и локальные патчи для каждого физически скопированного skill — здесь, не в теле `SKILL.md` (`ENGINEER/.claude/rules/capability-resolver.md`, «Слоистое монтирование»).

| Skill | Upstream source | Commit/tag/date донора | Technical adaptations | Local methodology patches | Last upstream review |
|---|---|---|---|---|---|
| `cro-auditor` | `github.com/thatrebeccarae` (Rebecca Rae Barton), MIT | [УТОЧНИТЬ] — не зафиксировано при копировании 2026-09-10 | Раздел переработан под единственную карточку Avito (LIFT + ICE), интеграция с `avito-listing-packaging`/`listing-copywriter` | Вырезаны funnel-анализ, форма/чекаут-оптимизация, email opt-in/capture-метрики, SaaS-бенчмарки конверсии — вне домена одной карточки на классифайде, не той же профессии; добавлен раздел «Формат вывода для Quality Gate Авитолога» (2026-09-23) | не проводилась |
| `lead-magnets` | `coreyhaines31/marketingskills`, MIT | [УТОЧНИТЬ] — не зафиксировано при копировании 2026-09-10 | Лид-магнит переопределён как файл/чек-лист в чат-воронке Avito, не email-gate/лендинг | Вырезаны email-gating, лендинги, платная реклама, B2B-метрики — у продукта нет форм/email-воронки/посадочных страниц | не проводилась |
| `ru-text-critic` | Физическая копия из `AI Copywriter/.claude/skills/ru-text-critic/` (внутренняя сборка того дома: `v0lka/skills@humanize-ru` + внутренний `russian-critic-protocol`/`critic-rubric` + `Halfofthesky/nora-gal`, MIT/внутреннее) | 2026-09-23 — заменил `humanizer` (MIT, `blader/humanizer`, английский, неприменим к русскому тексту — решение владельца 2026-09-23) | Без изменений методики при переносе | нет | не проводилась |

Остальные skills (`avito-listing-packaging`, `listing-copywriter`, `listing-seo-geo`, `listing-fin-offer`, `traffic-calculator`, `market-recon`, `media-plan`) — собраны в доме с нуля, не физическая копия внешнего репозитория, provenance-запись не требуется.
