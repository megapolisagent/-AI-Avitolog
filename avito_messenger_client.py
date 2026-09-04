#!/usr/bin/env python3
"""AvitoMessengerClient — чтение чатов и отправка автоответов.

СТАТУС ИСТОЧНИКА: endpoint'ы Messenger (get_chats/get_messages/send_auto_reply)
НЕ подтверждены официальным файлом от Avito (в отличие от Authorization и
Promotion API, которые Engineer сверила по реальным Swagger-файлам из личного
кабинета владельца). Спецификация получена только текстом от владельца.
Проверять по порядку снизу вверх — от самого дешёвого/безопасного вызова
к самому рискованному, не пропускать шаги.
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
LEADS_DIR = BASE_DIR / "data" / "leads"
SENT_LOG_PATH = BASE_DIR / "data" / ".autoreply_sent.json"

API_BASE = "https://api.avito.ru"


def _mask(secret):
    if not secret or len(secret) < 8:
        return "***"
    return secret[:4] + "…" + secret[-4:]


class AvitoMessengerClient:
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
        self._self_id = None

    def _request(self, method, path, headers=None, data=None, is_form=False, query=None):
        url = f"{API_BASE}{path}"
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        body = None
        if data is not None:
            body = data if is_form else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
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
        print(f"[avito] токен получен ({_mask(self._token)})")
        return self._token

    def _auth_headers(self):
        if not self._token or time.time() > self._expires_at - 60:
            self.get_token()
        return {"Authorization": f"Bearer {self._token}"}

    def get_self_id(self):
        if self._self_id:
            return self._self_id
        status, resp = self._request("GET", "/core/v1/accounts/self", headers=self._auth_headers())
        if status != 200:
            raise SystemExit(f"get_self_id не сработал (status {status}): {resp} — эта часть спецификации не подтвердилась.")
        self._self_id = resp.get("id")
        print(f"[avito] user_id получен: {self._self_id}")
        return self._self_id

    def get_chats(self, unread_only=False, limit=50, offset=0):
        user_id = self.get_self_id()
        query = {"limit": limit, "offset": offset}
        if unread_only:
            query["unread_only"] = "true"
        status, resp = self._request(
            "GET", f"/messenger/v2/accounts/{user_id}/chats",
            headers=self._auth_headers(), query=query,
        )
        if status != 200:
            raise SystemExit(f"get_chats не сработал (status {status}): {resp}")
        return resp.get("chats", [])

    def get_messages(self, chat_id, limit=50, offset=0):
        user_id = self.get_self_id()
        status, resp = self._request(
            "GET", f"/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages",
            headers=self._auth_headers(), query={"limit": limit, "offset": offset},
        )
        if status != 200:
            raise SystemExit(f"get_messages не сработал (status {status}): {resp}")
        return resp if isinstance(resp, list) else resp.get("messages", [])

    def send_auto_reply(self, chat_id, text, max_retries=3):
        """Отправка с retry (экспоненциальный backoff) вместо падения всего процесса
        на первом сбое — сбой по одному чату не должен обрывать обработку остальных
        (C-1, аудит Codex 2026-09-02). При исчерпании попыток — лог в stderr,
        возврат None (не SystemExit), вызывающий код сам решает, что делать дальше.
        """
        user_id = self.get_self_id()
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                status, resp = self._request(
                    "POST", f"/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages",
                    headers=headers, data={"message": {"text": text}, "type": "text"},
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_error = f"сетевая ошибка: {e}"
            else:
                if status == 200:
                    print(f"[avito] автоответ отправлен в чат {chat_id}, msg id {resp.get('id')}")
                    return resp
                last_error = f"status {status}: {resp}"
            if attempt < max_retries:
                wait = 2 ** (attempt - 1)
                print(f"[avito] send_auto_reply чат {chat_id}, попытка {attempt}/{max_retries} не удалась ({last_error}), повтор через {wait}с", file=sys.stderr)
                time.sleep(wait)
        print(f"[avito] send_auto_reply НЕ УДАЛСЯ после {max_retries} попыток, чат {chat_id}: {last_error}", file=sys.stderr)
        return None


def _load_sent_log():
    if SENT_LOG_PATH.exists():
        try:
            return json.loads(SENT_LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_sent_log(log):
    SENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENT_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def _message_fingerprint(message):
    """Идентификатор входящего сообщения для дедупликации (C-2, аудит Codex 2026-09-02):
    отпечаток по id/дате сообщения, а не по факту "это первое сообщение в списке" — так
    повторный запуск --poll-and-reply не отправит второй автоответ на то же сообщение."""
    return str(message.get("id") or message.get("created") or message.get("created_at")
               or json.dumps(message, sort_keys=True, ensure_ascii=False))


def _extract_text(message):
    """Текст входящего сообщения. ПРЕДПОЛАГАЮ форму content.text (типовая форма Avito
    Messenger API) — не подтверждено официальным swagger-файлом, как и остальные
    endpoint'ы этого клиента (см. заголовок файла). Если форма другая — вернёт "",
    detect_reply тогда честно уйдёт в DEFAULT_AUTOREPLY, не упадёт и не придумает текст."""
    content = message.get("content") or {}
    if isinstance(content, dict) and content.get("text"):
        return content["text"]
    return message.get("text") or ""


def log_lead(chat_id, message):
    LEADS_DIR.mkdir(parents=True, exist_ok=True)
    path = LEADS_DIR / f"{time.strftime('%Y-%m-%d')}-leads.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "chat_id": chat_id, "message": message,
        }, ensure_ascii=False) + "\n")


DEFAULT_AUTOREPLY = (
    "Здравствуйте! Спасибо за интерес — подскажите, пожалуйста, что для вас сейчас "
    "важнее: посмотреть квартиру, узнать условия покупки или что-то ещё? Отвечу "
    "конкретно по вашему вопросу."
)

# Исправлено (аудит Codex, 2026-09-02, CRITICAL C-2): раньше один и тот же текст уходил
# на любое входящее сообщение, независимо от вопроса покупателя — риск ответить "полная
# стоимость или ипотека" человеку, который спросил "можно посмотреть завтра?".
#
# Честно про метод: это НЕ распознавание намерения через модель — здесь нет вызова LLM,
# это отдельный CLI-скрипт без доступа к Claude/API-модели. Это классификация по ключевым
# словам (тот же класс метода, что уже использует guess_rooms в generate_feed.py) — узнаёт
# конкретный часто задаваемый вопрос, не "понимает" сообщение целиком. Если ни один паттерн
# не совпал — уходит DEFAULT_AUTOREPLY, который сам является уточняющим вопросом
# (Clarification Protocol), а не готовым расчётом наугад.
INTENT_PATTERNS = [
    (
        re.compile(r"документ|собственник|юридич|чист.*сделк|договор", re.IGNORECASE),
        "Здравствуйте! По документам — какие именно вас интересуют (право собственности, "
        "разрешение на строительство, договор с застройщиком)? Пришлю то, что нужно.",
    ),
    (
        re.compile(r"ипотек|рассрочк|кредит|первоначальн.*взнос|в кредит", re.IGNORECASE),
        "Здравствуйте! По ипотеке/рассрочке — подскажите, у вас уже есть одобрение "
        "по ипотеке или рассматриваете рассрочку от застройщика? Дам точные условия под ваш случай.",
    ),
    (
        re.compile(r"скидк|торг|дешевле|снизит|дороговат", re.IGNORECASE),
        "Здравствуйте! По цене — расскажите, пожалуйста, какой бюджет вы рассматриваете, "
        "и я скажу, какие варианты (рассрочка/акции застройщика) реально применимы к этому лоту.",
    ),
    (
        re.compile(r"актуал|ещё\s*прода|ещё\s*в\s*наличии", re.IGNORECASE),
        "Здравствуйте! Да, лот актуален. Хотите, пришлю свежую презентацию с планировкой "
        "и текущими условиями?",
    ),
    (
        re.compile(r"посмотр|показ|приехать|когда можно|встрет", re.IGNORECASE),
        "Здравствуйте! Да, конечно — подскажите, пожалуйста, в какой день и в какое "
        "время вам удобно приехать на просмотр, подберу свободный слот.",
    ),
]


def detect_reply(message_text):
    """Подбирает ответ по ключевым словам входящего сообщения (см. примечание к
    INTENT_PATTERNS выше). Пустой/нечитаемый текст — явный сигнал вернуть уточняющий
    DEFAULT_AUTOREPLY, не гадать (Doubt Protocol)."""
    text = (message_text or "").strip()
    if not text:
        return DEFAULT_AUTOREPLY
    for pattern, reply in INTENT_PATTERNS:
        if pattern.search(text):
            return reply
    return DEFAULT_AUTOREPLY

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-token", action="store_true")
    parser.add_argument("--check-self-id", action="store_true")
    parser.add_argument("--check-chats", action="store_true")
    parser.add_argument("--poll-and-reply", action="store_true")
    args = parser.parse_args()

    client = AvitoMessengerClient()
    if args.check_token:
        client.get_token(force_refresh=True)
    elif args.check_self_id:
        client.get_self_id()
    elif args.check_chats:
        chats = client.get_chats()
        print(f"[avito] чатов найдено: {len(chats)}")
    elif args.poll_and_reply:
        sent_log = _load_sent_log()
        for chat in client.get_chats(unread_only=True):
            chat_id = chat.get("id")
            messages = client.get_messages(chat_id)
            if not messages or messages[0].get("direction") != "in":
                continue
            fingerprint = _message_fingerprint(messages[0])
            if sent_log.get(str(chat_id)) == fingerprint:
                print(f"[avito] чат {chat_id}: автоответ на это сообщение уже отправлен, пропуск")
                continue
            log_lead(chat_id, messages[0])
            reply_text = detect_reply(_extract_text(messages[0]))
            result = client.send_auto_reply(chat_id, reply_text)
            if result is not None:
                sent_log[str(chat_id)] = fingerprint
                _save_sent_log(sent_log)
