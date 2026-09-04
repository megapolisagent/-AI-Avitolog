#!/usr/bin/env python3
"""AvitoAPIClient — базовый клиент авторизации для официального Avito API.

Источник схемы: knowledge/reference/00_core/avito-api-authorization-swagger.json
(официальный swagger, не предположение). OAuth 2.0, grant_type=client_credentials,
POST /token -> Bearer-токен на 24 часа. Никакого скрейпинга/парсинга HTML —
только REST-запросы к api.avito.ru.

Назначение: общий фундамент (авторизация + токен-кэш + HTTP-обёртка) для
модулей, работающих с конкретными разделами API (автозагрузка, статистика,
мессенджер и т.д.) — сам по себе методов чтения объявлений/выгрузки не содержит,
это следующий шаг поверх уже проверенной авторизации.
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_CACHE_PATH = BASE_DIR / ".avito_token_cache.json"
REPORTS_DIR = BASE_DIR / "data" / "avito_reports"
API_BASE = "https://api.avito.ru"


def _mask(secret):
    if not secret or len(secret) < 8:
        return "***"
    return secret[:4] + "…" + secret[-4:]


class AvitoAPIClient:
    def __init__(self):
        self.client_id = os.getenv("AVITO_CLIENT_ID")
        self.client_secret = os.getenv("AVITO_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise SystemExit(
                "AVITO_CLIENT_ID / AVITO_CLIENT_SECRET не заданы в окружении — "
                "заполните .env и подгрузите его перед запуском (`set -a; source .env; set +a`)."
            )
        self._token = None
        self._expires_at = 0

    def _request(self, method, path, headers=None, data=None, is_form=False, query=None):
        url = f"{API_BASE}{path}"
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        headers = dict(headers or {})
        body = None
        if data is not None:
            if is_form:
                body = data
            else:
                body = json.dumps(data).encode("utf-8")
                # Найдено реальным тестом 2026-09-04 (400 "invalid content type") — ни один
                # метод до аналитики/calltracking/отзывов/спецпредложений не отправлял JSON-
                # тело сам, поэтому auth_headers() никогда не включал Content-Type и баг не
                # проявлялся. Ставим здесь один раз, не в каждом новом методе отдельно.
                headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"error": raw}

    def _load_cached_token(self):
        if TOKEN_CACHE_PATH.exists():
            try:
                cache = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
                if cache.get("expires_at", 0) > time.time() + 60:
                    return cache["access_token"], cache["expires_at"]
            except (json.JSONDecodeError, KeyError):
                pass
        return None, 0

    def get_token(self, force_refresh=False):
        if not force_refresh:
            cached, expires_at = self._load_cached_token()
            if cached:
                self._token, self._expires_at = cached, expires_at
                return self._token

        body = (
            f"grant_type=client_credentials"
            f"&client_id={self.client_id}"
            f"&client_secret={self.client_secret}"
        ).encode("utf-8")
        status, resp = self._request(
            "POST", "/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body, is_form=True,
        )
        if status != 200 or "access_token" not in resp:
            raise SystemExit(f"Токен не получен (status {status}): {resp}")
        self._token = resp["access_token"]
        self._expires_at = time.time() + resp.get("expires_in", 86400)
        TOKEN_CACHE_PATH.write_text(json.dumps({
            "access_token": self._token, "expires_at": self._expires_at,
        }), encoding="utf-8")
        print(f"[avito] токен получен ({_mask(self._token)}), истекает через {resp.get('expires_in', 86400)} с")
        return self._token

    def auth_headers(self):
        if not self._token or time.time() > self._expires_at - 60:
            self.get_token()
        return {"Authorization": f"Bearer {self._token}"}

    def get_self_id(self):
        status, resp = self._request("GET", "/core/v1/accounts/self", headers=self.auth_headers())
        if status != 200:
            raise SystemExit(f"get_self_id не сработал (status {status}): {resp}")
        return resp.get("id")

    @staticmethod
    def _save_raw_log(name, payload):
        # Найдено реальным тестом 2026-09-04: RFC3339-таймстампы в name (calltracking/
        # спецпредложения) содержат ':' — запрещённый символ в имени файла на Windows,
        # падало с OSError после уже успешного вызова API. Санитизация здесь одна на всех
        # вызывающих, не в каждом методе по отдельности.
        safe_name = re.sub(r'[:<>"/\\|?*]', "-", name)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}-{safe_name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # --- Items API (источник: knowledge/reference/03_analytics_market/avito-api-items-stats.json) ---

    def get_items(self, status="active", per_page=100, page=1):
        """Список своих объявлений: id/price/status/address/category. GET /core/v1/items.
        Не работает для сотрудников (см. описание метода в swagger) — только для владельца аккаунта.
        """
        status_, resp = self._request(
            "GET", "/core/v1/items", headers=self.auth_headers(),
            query={"status": status, "per_page": per_page, "page": page},
        )
        if status_ != 200:
            raise SystemExit(f"get_items не сработал (status {status_}): {resp}")
        self._save_raw_log("items", resp)
        return resp

    def get_item_info(self, item_id):
        """Детали одного объявления: status/vas/дата создания/URL.
        GET /core/v1/accounts/{user_id}/items/{item_id}/.
        ВНИМАНИЕ (подтверждено swagger, не предположение): этот эндпоинт НЕ отдаёт
        цену и статистику просмотров/контактов. Цену объявления смотри в get_items()
        (поле price там есть). Статистика просмотров/контактов — отдельный метод
        itemAnalytics (POST /stats/v2/accounts/{user_id}/items) с обязательным
        диапазоном дат — не включена сюда, чтобы не выдумывать дефолтный период.
        """
        user_id = self.get_self_id()
        status, resp = self._request(
            "GET", f"/core/v1/accounts/{user_id}/items/{item_id}/", headers=self.auth_headers(),
        )
        if status != 200:
            raise SystemExit(f"get_item_info не сработал (status {status}): {resp}")
        self._save_raw_log(f"item-{item_id}", resp)
        return resp

    # --- Расширенная аналитика (источник: knowledge/reference/03_analytics_market/avito-api-items-stats.json) ---
    # Раньше упоминалась только в комментарии ("не включена сюда, чтобы не выдумывать дефолтный
    # период") — теперь реально реализована, с обязательными явными датами, не дефолтом.

    def get_items_analytics(self, date_from, date_to, metrics, grouping="totals",
                             limit=1000, offset=0, category_ids=None, employee_ids=None):
        """Расширенная аналитика по объявлениям (просмотры/контакты/избранное за период).
        POST /stats/v2/accounts/{user_id}/items. Схема (AnalyticsRequest, swagger) требует
        явно: dateFrom, dateTo, metrics (например ["views","contacts"] — конкретный список,
        не выдумывается дефолтом), grouping (day/week/month/item/totals), limit, offset."""
        user_id = self.get_self_id()
        body = {
            "dateFrom": date_from, "dateTo": date_to,
            "metrics": metrics, "grouping": grouping,
            "limit": limit, "offset": offset,
        }
        filt = {}
        if category_ids:
            filt["categoryIDs"] = category_ids
        if employee_ids:
            filt["employeeIDs"] = employee_ids
        if filt:
            body["filter"] = filt
        status, resp = self._request(
            "POST", f"/stats/v2/accounts/{user_id}/items", headers=self.auth_headers(), data=body,
        )
        if status != 200:
            raise SystemExit(f"get_items_analytics не сработал (status {status}): {resp}")
        self._save_raw_log(f"items-analytics-{date_from}_{date_to}", resp)
        return resp

    def get_spendings(self, date_from, date_to, spending_types, grouping="month",
                       item_ids=None, category_ids=None, location_ids=None):
        """Расходы на продвижение за период. POST /stats/v2/accounts/{user_id}/spendings.
        Схема (SpendingsRequest, swagger) требует явно: dateFrom, dateTo, spendingTypes
        (all/promotion/presence/commission/rest — конкретный список, не дефолт), grouping —
        ВНИМАНИЕ: свой enum SpendingsGroupings (только day/week/month), не тот же Groupings,
        что у get_items_analytics (day/week/month/item/totals) — разные схемы, перепутать
        легко, найдено реальным тестом 2026-09-04 ("totals" здесь невалиден)."""
        user_id = self.get_self_id()
        body = {
            "dateFrom": date_from, "dateTo": date_to,
            "spendingTypes": spending_types, "grouping": grouping,
        }
        filt = {}
        if item_ids:
            filt["itemIDs"] = item_ids
        if category_ids:
            filt["categoryIDs"] = category_ids
        if location_ids:
            filt["locationIDs"] = location_ids
        if filt:
            body["filter"] = filt
        status, resp = self._request(
            "POST", f"/stats/v2/accounts/{user_id}/spendings", headers=self.auth_headers(), data=body,
        )
        if status != 200:
            raise SystemExit(f"get_spendings не сработал (status {status}): {resp}")
        self._save_raw_log(f"spendings-{date_from}_{date_to}", resp)
        return resp

    # --- Аналитика по недвижимости (источник: knowledge/reference/03_analytics_market/avito-api-realty-analytics.json) ---
    # ВАЖНО (проверено 2026-09-04, не предположение): доступ к обоим методам ограничен —
    # "доступен только партнёрам Avito с подтверждённым коммерческим использованием"
    # (описание в самом swagger). Ответ содержит явный флаг featureDisabled — метод может
    # технически существовать, но быть выключен на конкретном аккаунте. Проверять по этому
    # флагу, не по факту успешного вызова токена.

    def get_market_price_correspondence(self, item_id, price):
        """Проверка, соответствует ли цена объявления рыночной. GET /realty/v1/
        marketPriceCorrespondence/{itemId}/{price}. Требует партнёрского доступа Avito —
        см. предупреждение выше."""
        status, resp = self._request(
            "GET", f"/realty/v1/marketPriceCorrespondence/{item_id}/{price}", headers=self.auth_headers(),
        )
        if status != 200:
            raise SystemExit(f"get_market_price_correspondence не сработал (status {status}): {resp}")
        self._save_raw_log(f"market-price-{item_id}", resp)
        return resp

    def create_realty_report(self, item_id):
        """Аналитический отчёт по объявлению — рыночная позиция цены/условий. POST
        /realty/v1/report/create/{itemId}. Возвращает ссылку на отчёт (`reportLink`), не
        структурированные данные напрямую. Требует партнёрского доступа — см. предупреждение
        выше; при featureDisabled=true фича не включена на этом аккаунте, не ошибка вызова."""
        status, resp = self._request(
            "POST", f"/realty/v1/report/create/{item_id}", headers=self.auth_headers(),
        )
        if status != 200:
            raise SystemExit(f"create_realty_report не сработал (status {status}): {resp}")
        self._save_raw_log(f"realty-report-{item_id}", resp)
        return resp

    # --- CallTracking (источник: knowledge/reference/02_conversion_retention/avito-api-calltracking.json) ---

    def get_calls(self, date_time_from, date_time_to=None, limit=50, offset=0):
        """Список звонков по объявлениям за период. POST /calltracking/v1/getCalls/.
        date_time_from обязателен (RFC3339); без date_time_to Avito сам берёт
        date_time_from + 1 месяц (максимум — 3 месяца, ограничение API, не выдумано)."""
        body = {"dateTimeFrom": date_time_from, "limit": limit, "offset": offset}
        if date_time_to:
            body["dateTimeTo"] = date_time_to
        status, resp = self._request(
            "POST", "/calltracking/v1/getCalls/", headers=self.auth_headers(), data=body,
        )
        if status != 200:
            raise SystemExit(f"get_calls не сработал (status {status}): {resp}")
        self._save_raw_log(f"calls-{date_time_from}", resp)
        return resp

    def get_call_by_id(self, call_id):
        """Детали одного звонка. POST /calltracking/v1/getCallById/."""
        status, resp = self._request(
            "POST", "/calltracking/v1/getCallById/", headers=self.auth_headers(), data={"callId": call_id},
        )
        if status != 200:
            raise SystemExit(f"get_call_by_id не сработал (status {status}): {resp}")
        self._save_raw_log(f"call-{call_id}", resp)
        return resp

    def get_call_record(self, call_id):
        """Ссылка на запись звонка. GET /calltracking/v1/getRecordByCallId/."""
        status, resp = self._request(
            "GET", "/calltracking/v1/getRecordByCallId/", headers=self.auth_headers(),
            query={"callId": call_id},
        )
        if status != 200:
            raise SystemExit(f"get_call_record не сработал (status {status}): {resp}")
        self._save_raw_log(f"call-record-{call_id}", resp)
        return resp

    # --- Отзывы и рейтинг (источник: knowledge/reference/02_conversion_retention/avito-api-ratings-reviews.json) ---

    def get_rating_info(self):
        """Общая информация о рейтинге аккаунта. GET /ratings/v1/info."""
        status, resp = self._request("GET", "/ratings/v1/info", headers=self.auth_headers())
        if status != 200:
            raise SystemExit(f"get_rating_info не сработал (status {status}): {resp}")
        self._save_raw_log("rating-info", resp)
        return resp

    def get_reviews(self, offset=0, limit=50):
        """Список отзывов покупателей. GET /ratings/v1/reviews."""
        status, resp = self._request(
            "GET", "/ratings/v1/reviews", headers=self.auth_headers(),
            query={"offset": offset, "limit": limit},
        )
        if status != 200:
            raise SystemExit(f"get_reviews не сработал (status {status}): {resp}")
        self._save_raw_log(f"reviews-{offset}", resp)
        return resp

    def answer_review(self, review_id, message):
        """Ответить на отзыв. POST /ratings/v1/answers.
        МУТИРУЮЩИЙ метод — публикует текст публично под отзывом. Не вызывать без
        подтверждения владельца текста ответа (тот же принцип, что Advisory First —
        `repository-design/SKILL.md`), этот клиент только даёт техническую возможность."""
        status, resp = self._request(
            "POST", "/ratings/v1/answers", headers=self.auth_headers(),
            data={"reviewId": review_id, "message": message},
        )
        if status != 200:
            raise SystemExit(f"answer_review не сработал (status {status}): {resp}")
        self._save_raw_log(f"review-answer-{review_id}", resp)
        return resp

    def delete_review_answer(self, answer_id):
        """Удалить свой ответ на отзыв. DELETE /ratings/v1/answers/{answer_id}. МУТИРУЮЩИЙ."""
        status, resp = self._request(
            "DELETE", f"/ratings/v1/answers/{answer_id}", headers=self.auth_headers(),
        )
        if status != 200:
            raise SystemExit(f"delete_review_answer не сработал (status {status}): {resp}")
        return resp

    # --- Спецпредложения в чате, beta (источник: knowledge/reference/02_conversion_retention/avito-api-messenger-offers-beta.json) ---
    # ВНИМАНИЕ: multiCreate/multiConfirm — МУТИРУЮЩИЕ, реально отправляют предложение
    # покупателю. available/stats/tariff_info — read-only, безопасны для разведки.

    def get_offers_available(self, item_ids):
        """Проверить, доступны ли спецпредложения для списка объявлений. POST
        /special-offers/v1/available. Read-only."""
        status, resp = self._request(
            "POST", "/special-offers/v1/available", headers=self.auth_headers(),
            data={"itemIds": item_ids},
        )
        if status != 200:
            raise SystemExit(f"get_offers_available не сработал (status {status}): {resp}")
        self._save_raw_log("offers-available", resp)
        return resp

    def get_offers_stats(self, date_time_from, date_time_to):
        """Статистика по уже отправленным спецпредложениям за период. POST
        /special-offers/v1/stats. Read-only."""
        status, resp = self._request(
            "POST", "/special-offers/v1/stats", headers=self.auth_headers(),
            data={"dateTimeFrom": date_time_from, "dateTimeTo": date_time_to},
        )
        if status != 200:
            raise SystemExit(f"get_offers_stats не сработал (status {status}): {resp}")
        self._save_raw_log(f"offers-stats-{date_time_from}", resp)
        return resp

    def get_offers_tariff_info(self):
        """Тариф на спецпредложения. POST /special-offers/v1/tariffInfo. Read-only."""
        status, resp = self._request("POST", "/special-offers/v1/tariffInfo", headers=self.auth_headers())
        if status != 200:
            raise SystemExit(f"get_offers_tariff_info не сработал (status {status}): {resp}")
        return resp

    def create_offers(self, item_ids):
        """Создать спецпредложение по списку объявлений. POST /special-offers/v1/multiCreate.
        МУТИРУЮЩИЙ — не вызывать без явного решения владельца, что предложить конкретным
        покупателям (тот же Advisory First принцип, что answer_review выше)."""
        status, resp = self._request(
            "POST", "/special-offers/v1/multiCreate", headers=self.auth_headers(),
            data={"itemIds": item_ids},
        )
        if status != 200:
            raise SystemExit(f"create_offers не сработал (status {status}): {resp}")
        self._save_raw_log("offers-create", resp)
        return resp

    def confirm_offers(self, dispatches, expires_at=None):
        """Подтвердить и отправить созданные спецпредложения. POST
        /special-offers/v1/multiConfirm. МУТИРУЮЩИЙ — реально отправляет покупателю."""
        body = {"dispatches": dispatches}
        if expires_at:
            body["expiresAt"] = expires_at
        status, resp = self._request(
            "POST", "/special-offers/v1/multiConfirm", headers=self.auth_headers(), data=body,
        )
        if status != 200:
            raise SystemExit(f"confirm_offers не сработал (status {status}): {resp}")
        self._save_raw_log("offers-confirm", resp)
        return resp

    # --- Autoload API (источник: knowledge/reference/04_feed_technical/avito-api-autoload.json) ---

    def get_autoload_reports(self, per_page=10, page=1):
        """История и статусы последних выгрузок фида. GET /autoload/v4/uploads (актуальный,
        не deprecated метод — v2/v3 reports помечены в swagger deprecated, отключаются 08.03.2027).
        """
        status, resp = self._request(
            "GET", "/autoload/v4/uploads", headers=self.auth_headers(),
            query={"perPage": per_page, "page": page},
        )
        if status != 200:
            raise SystemExit(f"get_autoload_reports не сработал (status {status}): {resp}")
        self._save_raw_log("autoload-uploads", resp)
        return resp

    def get_latest_upload_details(self, which="last_successful", per_page=100, page=1):
        """Детали последней выгрузки фида — сводка + постатейные ошибки/отклонённые лоты,
        без deprecated v2. GET /autoload/v4/uploads/{which} + /autoload/v4/uploads/{which}/items,
        which: "last_successful" (стабильные данные, дефолт) или "current" (свежее, может ещё
        обрабатываться — см. описание метода в knowledge/reference/04_feed_technical/avito-api-autoload.json).
        Закрывает большинство реальных случаев без обращения к report_id вообще — миграция
        с deprecated Autoload v2 (аудит Codex, 2026-09-02, CRITICAL C-3; дедлайн сокращения
        данных 08.09.2026, отключение 08.03.2027 — зафиксировано в OPEN_QUESTIONS.md).
        """
        if which not in ("last_successful", "current"):
            raise ValueError('which должен быть "last_successful" или "current"')
        headers = self.auth_headers()
        status, summary = self._request("GET", f"/autoload/v4/uploads/{which}", headers=headers)
        if status == 404:
            return {"which": which, "summary": None, "items": None, "note": "подходящей загрузки нет"}
        if status != 200:
            raise SystemExit(f"get_latest_upload_details (summary) не сработал (status {status}): {summary}")
        status, items = self._request(
            "GET", f"/autoload/v4/uploads/{which}/items", headers=headers,
            query={"perPage": per_page, "page": page},
        )
        if status != 200:
            raise SystemExit(f"get_latest_upload_details (items) не сработал (status {status}): {items}")
        result = {"which": which, "summary": summary, "items": items}
        self._save_raw_log(f"autoload-upload-{which}", result)
        return result

    def get_autoload_report_details(self, report_id):
        """LEGACY — только для произвольного исторического report_id, которого v4 не отдаёт
        (v4 знает только "текущая"/"последняя успешная" выгрузка, см. get_latest_upload_details —
        предпочитай его для обычного случая "что с моей последней выгрузкой"). GET
        /autoload/v2/reports/{report_id} + GET /autoload/v2/reports/{report_id}/items.
        ВНИМАНИЕ: оба метода помечены в swagger deprecated — с 08.09.2026 отдают меньше данных,
        отключаются 08.03.2027 (см. OPEN_QUESTIONS.md — миграция отслеживается с дедлайном).
        """
        print("[avito] ПРЕДУПРЕЖДЕНИЕ: /autoload/v2/reports/* deprecated, с 08.09.2026 меньше данных, отключение 08.03.2027", file=sys.stderr)
        headers = self.auth_headers()
        status, summary = self._request("GET", f"/autoload/v2/reports/{report_id}", headers=headers)
        if status != 200:
            raise SystemExit(f"get_autoload_report_details (summary) не сработал (status {status}): {summary}")
        status, items = self._request(
            "GET", f"/autoload/v2/reports/{report_id}/items", headers=headers,
            query={"per_page": 200, "page": 0},
        )
        if status != 200:
            raise SystemExit(f"get_autoload_report_details (items) не сработал (status {status}): {items}")
        result = {"report_id": report_id, "summary": summary, "items": items}
        self._save_raw_log(f"autoload-report-{report_id}", result)
        return result

    def get_autoload_fee_info(self):
        """Баланс кошелька (реальные + бонусные средства). GET /core/v1/accounts/{user_id}/balance/.
        ЧЕСТНО: отдельного эндпоинта "пакеты размещения под автозагрузку" в доступных swagger-файлах
        НЕ найдено — package_id встречается только внутри постатейных списаний конкретной выгрузки
        (deprecated /autoload/v2/reports/{report_id}/items/fees), не как самостоятельный список пакетов.
        """
        user_id = self.get_self_id()
        status, resp = self._request(
            "GET", f"/core/v1/accounts/{user_id}/balance/", headers=self.auth_headers(),
        )
        if status != 200:
            raise SystemExit(f"get_autoload_fee_info не сработал (status {status}): {resp}")
        self._save_raw_log("balance", resp)
        return resp


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Базовый тест авторизации Avito API")
    parser.add_argument("--check-token", action="store_true", help="получить/обновить access token")
    parser.add_argument("--check-self-id", action="store_true", help="проверить токен вызовом /core/v1/accounts/self")
    parser.add_argument("--get-items", action="store_true", help="получить список активных объявлений аккаунта")
    args = parser.parse_args()

    client = AvitoAPIClient()
    if args.check_token:
        client.get_token(force_refresh=True)
    elif args.check_self_id:
        user_id = client.get_self_id()
        print(f"[avito] user_id: {user_id}")
    elif args.get_items:
        data = client.get_items(status="active")
        resources = data.get("resources", [])
        print(f"[avito] активных объявлений: {len(resources)}")
        for it in resources[:10]:
            print(f"  id={it.get('id')} price={it.get('price')} status={it.get('status')} {it.get('address')}")
    else:
        parser.print_help()
