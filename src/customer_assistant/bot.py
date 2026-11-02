"""Telegram loop: receive, debounce, answer concurrently per customer."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)

MAX_REPLY_LENGTH = 4096
NON_TEXT_REPLY = "I can only read text messages at the moment. Please type your question."
FOLLOW_UP_INTERVAL_SECONDS = 300


class TelegramApiError(RuntimeError):
    pass


class BotApi:
    def __init__(self, token: str, session: Optional[requests.Session] = None):
        self._base = f"https://api.telegram.org/bot{token}"
        self._session = session or requests.Session()

    def _call(self, method: str, payload: dict, timeout: float):
        try:
            response = self._session.post(f"{self._base}/{method}", json=payload, timeout=timeout)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            # The request URL contains the bot token, so the original message must not be logged.
            raise TelegramApiError(f"{method} failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise TelegramApiError(f"{method} failed: {data.get('description', 'unknown error')}")
        return data["result"]

    def get_updates(self, offset: Optional[int], timeout: int) -> list:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout + 10)

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_REPLY_LENGTH]}, 15)

    def send_typing(self, chat_id: int) -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, 15)


@dataclass
class _Pending:
    parts: list = field(default_factory=list)
    last_at: float = 0.0


class Debouncer:
    """Combine messages sent in quick succession into a single request."""

    def __init__(self, wait_seconds: float = 3.0):
        self._wait = wait_seconds
        self._pending = {}

    def add(self, user_id: int, text: str, now: float) -> None:
        pending = self._pending.setdefault(user_id, _Pending())
        pending.parts.append(text)
        pending.last_at = now

    def due(self, now: float, busy: set) -> list:
        """Return (user_id, text, count) for quiet customers with no running request."""
        ready = []
        for user_id, pending in list(self._pending.items()):
            if user_id not in busy and now - pending.last_at >= self._wait:
                ready.append((user_id, "\n".join(pending.parts), len(pending.parts)))
                del self._pending[user_id]
        return ready


class Bot:
    def __init__(self, api, assistant, store, debouncer: Optional[Debouncer] = None, workers: int = 4,
                 follow_ups: bool = False, clock=time.monotonic):
        self._api = api
        self._assistant = assistant
        self._store = store
        self._debouncer = debouncer or Debouncer()
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._futures = {}
        self._follow_ups = follow_ups
        self._clock = clock
        self._last_follow_up = 0.0
        self.running = True

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        # Private chats only: the assistant never reads or answers in groups.
        if not message or message.get("chat", {}).get("type") != "private":
            return
        chat_id = message["chat"]["id"]
        text = (message.get("text") or message.get("caption") or "").strip()
        if text:
            self._debouncer.add(chat_id, text, self._clock())
        elif "sticker" not in message:
            self._safely(self._api.send_message, chat_id, NON_TEXT_REPLY)

    def _safely(self, call, *args) -> bool:
        try:
            call(*args)
            return True
        except TelegramApiError as exc:
            log.warning("%s", exc)
            return False

    def _answer(self, user_id: int, text: str, count: int) -> None:
        self._safely(self._api.send_typing, user_id)
        reply = self._assistant.respond(user_id, text, count)
        self._safely(self._api.send_message, user_id, reply)

    def dispatch(self) -> None:
        for user_id, future in list(self._futures.items()):
            if future.done():
                del self._futures[user_id]
                if future.exception():
                    log.error("Request failed: %s", type(future.exception()).__name__)
        for user_id, text, count in self._debouncer.due(self._clock(), set(self._futures)):
            self._futures[user_id] = self._pool.submit(self._answer, user_id, text, count)

    def send_follow_ups(self) -> None:
        for user_id, text in self._assistant.follow_up_due():
            # Marked only after delivery, and at most once per customer.
            if self._safely(self._api.send_message, user_id, text):
                self._assistant.mark_followed_up(user_id, text)

    def step(self, poll_timeout: int = 1) -> None:
        updates = self._api.get_updates(self._store.get_offset(), poll_timeout)
        for update in updates:
            self.handle_update(update)
            self._store.set_offset(update["update_id"] + 1)
        self.dispatch()
        now = self._clock()
        if self._follow_ups and now - self._last_follow_up >= FOLLOW_UP_INTERVAL_SECONDS:
            self._last_follow_up = now
            self.send_follow_ups()

    def run_forever(self) -> None:
        log.info("Customer assistant started")
        backoff = 1
        while self.running:
            try:
                self.step()
                backoff = 1
            except TelegramApiError as exc:
                log.warning("%s; retrying in %s s", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
        self._pool.shutdown(wait=True)
        log.info("Customer assistant stopped")
