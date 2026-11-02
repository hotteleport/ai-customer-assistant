import time

import pytest
import requests

from customer_assistant.bot import NON_TEXT_REPLY, Bot, BotApi, Debouncer, TelegramApiError
from customer_assistant.store import Store


class FakeApi:
    def __init__(self):
        self.sent = []
        self.typing = []
        self.batches = []
        self.offsets = []

    def get_updates(self, offset, timeout):
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_typing(self, chat_id):
        self.typing.append(chat_id)


class FakeAssistant:
    def __init__(self):
        self.calls = []
        self.follow_ups = []
        self.marked = []

    def respond(self, user_id, text, count):
        self.calls.append((user_id, text, count))
        return f"reply to {text}"

    def follow_up_due(self):
        return self.follow_ups

    def mark_followed_up(self, user_id, text):
        self.marked.append(user_id)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def update(update_id, chat_id=1, chat_type="private", **fields):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": chat_type}, **fields}}


@pytest.fixture
def parts(tmp_path):
    return FakeApi(), FakeAssistant(), Store(tmp_path / "db.sqlite3"), Clock()


def make(parts, **options):
    api, assistant, store, clock = parts
    return Bot(api, assistant, store, Debouncer(3), workers=2, clock=clock, **options)


def wait_for(condition):
    deadline = time.time() + 5
    while not condition() and time.time() < deadline:
        time.sleep(0.01)
    assert condition()


def test_debouncer_combines_quick_messages():
    debouncer = Debouncer(3)
    debouncer.add(1, "Hi", 0)
    debouncer.add(1, "How much?", 2)
    assert debouncer.due(4, set()) == []
    assert debouncer.due(5, set()) == [(1, "Hi\nHow much?", 2)]
    assert debouncer.due(9, set()) == []


def test_debouncer_waits_while_a_request_is_running():
    debouncer = Debouncer(3)
    debouncer.add(1, "Hi", 0)
    assert debouncer.due(9, {1}) == []
    assert debouncer.due(9, set()) == [(1, "Hi", 1)]


def test_private_message_is_answered_with_typing_status(parts):
    api, assistant, store, clock = parts
    bot = make(parts)
    api.batches.append([update(5, text="Hello")])
    bot.step()
    clock.now += 3
    bot.dispatch()
    wait_for(lambda: api.sent)
    assert api.sent == [(1, "reply to Hello")]
    assert api.typing == [1]
    assert store.get_offset() == 6


def test_group_messages_are_ignored(parts):
    api, assistant, store, clock = parts
    bot = make(parts)
    api.batches.append([update(5, chat_type="group", text="Hello")])
    bot.step()
    clock.now += 3
    bot.dispatch()
    assert assistant.calls == [] and api.sent == []
    assert store.get_offset() == 6


def test_non_text_gets_a_note_and_stickers_get_nothing(parts):
    api, *_ = parts
    bot = make(parts)
    bot.handle_update(update(1, photo=[{}]))
    bot.handle_update(update(2, sticker={}))
    assert api.sent == [(1, NON_TEXT_REPLY)]


def test_follow_up_is_marked_only_after_delivery(parts):
    api, assistant, *_ = parts
    bot = make(parts)
    assistant.follow_ups = [(1, "ok"), (2, "fails")]
    original = api.send_message

    def send(chat_id, text):
        if chat_id == 2:
            raise TelegramApiError("blocked")
        original(chat_id, text)

    api.send_message = send
    bot.send_follow_ups()
    assert assistant.marked == [1]


class BrokenSession:
    def post(self, url, json, timeout):
        raise requests.ConnectionError(f"could not reach {url}")


def test_network_error_never_exposes_the_token():
    with pytest.raises(TelegramApiError) as caught:
        BotApi("123:SECRET", BrokenSession()).get_updates(None, 1)
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
