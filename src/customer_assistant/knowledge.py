"""Select the knowledge base sections relevant to a question."""

import re
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)


def _words(text: str) -> set:
    return {word.casefold() for word in _WORD.findall(text)}


@dataclass(frozen=True)
class Section:
    title: str
    body: str

    def render(self) -> str:
        return f"## {self.title}\n{self.body}".strip()


def parse_sections(markdown: str) -> list:
    """Split a Markdown file into sections at each `## ` heading."""
    sections = []
    title = "General"
    lines = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if "".join(lines).strip():
                sections.append(Section(title, "\n".join(lines).strip()))
            title, lines = line[3:].strip(), []
        elif not line.startswith("# "):
            lines.append(line)
    if "".join(lines).strip():
        sections.append(Section(title, "\n".join(lines).strip()))
    return sections


class KnowledgeBase:
    def __init__(self, sections: list):
        self.sections = sections

    @classmethod
    def load(cls, path: Path) -> "KnowledgeBase":
        return cls(parse_sections(Path(path).read_text(encoding="utf-8")))

    def search(self, question: str, limit: int = 4) -> list:
        """Rank by shared words, with title matches counted twice."""
        terms = _words(question)
        scored = []
        for position, section in enumerate(self.sections):
            score = len(terms & _words(section.body)) + 2 * len(terms & _words(section.title))
            if score:
                scored.append((-score, position, section))
        return [section for _, _, section in sorted(scored, key=lambda item: item[:2])[:limit]]
