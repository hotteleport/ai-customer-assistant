"""Filters around the model.

These reduce noise and accidental leaks. They are not the security boundary:
pattern lists can always be bypassed, so the real protection is that the agent
has no tools, no credentials and no access to other customers' conversations.
"""

import re
from pathlib import Path

_INJECTION_PATTERNS = [
    r"ignore (all |your |the )?(previous|prior|above) (instructions|rules)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions|rules)",
    r"\bjailbreak\b",
    r"you are now\b",
    r"\b(admin|developer|owner) (mode|override)\b",
    r"игнорируй (все |свои |предыдущие )*(инструкции|правила)",
    r"покажи (свой |свои |твой |твои )?(промпт|инструкции|правила)",
]
_INJECTION = [re.compile(pattern, re.IGNORECASE) for pattern in _INJECTION_PATTERNS]
_MARKERS = re.compile(r"<\s*/?\s*(customer|history|knowledge)\b|system prompt", re.IGNORECASE)
MIN_LEAK_LENGTH = 40


def looks_like_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION)


def strip_leaks(reply: str, private_text: str = "") -> str:
    """Drop lines that expose prompt structure or quote the private prompt."""
    private_lines = {
        line.strip().casefold() for line in private_text.splitlines() if len(line.strip()) >= MIN_LEAK_LENGTH
    }
    kept = []
    for line in reply.splitlines():
        lowered = line.casefold()
        if _MARKERS.search(line) or any(private in lowered for private in private_lines):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def load_private_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")
