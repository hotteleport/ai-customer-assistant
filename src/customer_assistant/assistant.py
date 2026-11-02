"""Business logic for one customer turn and for follow-ups. No Telegram code here."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .agent import AgentError
from .guard import looks_like_injection, strip_leaks
from .knowledge import KnowledgeBase
from .store import Store

log = logging.getLogger(__name__)

STOP_COMMANDS = {"/stop", "stop", "unsubscribe"}
BLOCKED_REPLY = "I can only help with questions about our products and services."
LIMIT_REPLY = "You've reached today's message limit. Please write again tomorrow."
ERROR_REPLY = "Sorry, I can't answer right now. Please try again in a few minutes."
STOPPED_REPLY = "Done. I won't send you any more follow-up messages."
SUMMARISE_AFTER = 12
KEEP_RECENT = 6


@dataclass(frozen=True)
class Limits:
    daily_messages: int = 100
    follow_up_after_hours: float = 15
    follow_up_start_hour: int = 10
    follow_up_end_hour: int = 22
    follow_up_batch: int = 10


class Assistant:
    def __init__(self, store: Store, knowledge: KnowledgeBase, prompts, agent, limits: Limits = Limits(),
                 clock=time.time):
        self._store = store
        self._knowledge = knowledge
        self._prompts = prompts
        self._agent = agent
        self._limits = limits
        self._clock = clock

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")

    def _private_text(self) -> str:
        return self._prompts.system

    def respond(self, user_id: int, message: str, message_count: int = 1) -> str:
        """Return the reply for a customer's combined message."""
        if message.strip().casefold() in STOP_COMMANDS:
            self._store.set_opted_out(user_id)
            return STOPPED_REPLY
        if self._store.count_usage(user_id, self._day(), message_count) > self._limits.daily_messages:
            return LIMIT_REPLY

        now = self._clock()
        history = self._store.recent(user_id, KEEP_RECENT)
        self._store.add_message(user_id, "customer", message, now)
        if looks_like_injection(message):
            log.info("Blocked a suspected prompt injection")
            self._store.add_message(user_id, "assistant", BLOCKED_REPLY, now)
            return BLOCKED_REPLY

        prompt = self._prompts.reply(
            message, history, self._store.summary(user_id), self._knowledge.search(message)
        )
        try:
            reply = strip_leaks(self._agent.complete(prompt), self._private_text())
        except AgentError as exc:
            log.error("Agent failed: %s", exc)
            return ERROR_REPLY
        if not reply:
            return ERROR_REPLY

        self._store.add_message(user_id, "assistant", reply, self._clock())
        self._summarise_if_needed(user_id)
        return reply

    def _summarise_if_needed(self, user_id: int) -> None:
        old = self._store.messages_to_summarise(user_id, KEEP_RECENT)
        if len(old) < SUMMARISE_AFTER:
            return
        history = [(role, text) for _, role, text in old]
        try:
            summary = self._agent.complete(self._prompts.summarise(self._store.summary(user_id), history))
        except AgentError as exc:
            # The messages stay unsummarised, so the next turn simply tries again.
            log.warning("Summary failed: %s", exc)
            return
        if summary:
            self._store.apply_summary(user_id, summary, old[-1][0])

    def follow_up_due(self) -> list:
        """Prepare follow-ups as (user_id, text), inside allowed hours only."""
        now = self._clock()
        hour = datetime.fromtimestamp(now).hour
        if not self._limits.follow_up_start_hour <= hour < self._limits.follow_up_end_hour:
            return []
        cutoff = now - self._limits.follow_up_after_hours * 3600
        messages = []
        for user_id in self._store.follow_up_candidates(cutoff, self._limits.follow_up_batch):
            history = self._store.recent(user_id, KEEP_RECENT)
            topic = " ".join(text for role, text in history if role == "customer")
            prompt = self._prompts.follow_up(history, self._store.summary(user_id), self._knowledge.search(topic))
            try:
                text = strip_leaks(self._agent.complete(prompt), self._private_text())
            except AgentError as exc:
                log.warning("Follow-up failed: %s", exc)
                continue
            if text:
                messages.append((user_id, text))
        return messages

    def mark_followed_up(self, user_id: int, text: str) -> None:
        now = self._clock()
        self._store.add_message(user_id, "assistant", text, now)
        self._store.mark_followed_up(user_id, now)
