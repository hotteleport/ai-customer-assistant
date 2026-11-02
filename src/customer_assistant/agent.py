"""Build prompts and call Claude Code with every tool disabled.

Customer messages are untrusted. With no tools the model can only return text,
which the bot filters before sending.
"""

import subprocess
from pathlib import Path
from string import Template
from typing import Optional


class AgentError(RuntimeError):
    pass


def escape(text: str) -> str:
    """Stop untrusted text from closing the tags that mark it as data."""
    return (text or "").replace("<", "&lt;").replace(">", "&gt;")


def format_history(history: list) -> str:
    return "\n".join(f"{role}: {escape(text)}" for role, text in history)


class Prompts:
    """Loads prompts from a private directory kept out of the repository."""

    def __init__(self, directory: Path):
        directory = Path(directory)
        self.system = (directory / "system.md").read_text(encoding="utf-8")
        self._reply = Template((directory / "reply.md").read_text(encoding="utf-8"))
        self._summary = Template((directory / "summary.md").read_text(encoding="utf-8"))
        self._follow_up = Template((directory / "follow_up.md").read_text(encoding="utf-8"))

    def reply(self, message: str, history: list, summary: str, knowledge: list) -> str:
        return self._reply.substitute(
            message=escape(message),
            history=format_history(history),
            summary=escape(summary),
            knowledge="\n\n".join(section.render() for section in knowledge) or "No matching information.",
        )

    def summarise(self, previous: str, history: list) -> str:
        return self._summary.substitute(previous=escape(previous), history=format_history(history))

    def follow_up(self, history: list, summary: str, knowledge: list) -> str:
        return self._follow_up.substitute(
            history=format_history(history),
            summary=escape(summary),
            knowledge="\n\n".join(section.render() for section in knowledge),
        )


def build_command(system_prompt: str, model: Optional[str] = None) -> list:
    command = [
        "claude",
        "--print",
        "--output-format", "text",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
    ]
    if model:
        command += ["--model", model]
    return command


class ClaudeAgent:
    def __init__(self, system_prompt: str, workdir: Path, model: Optional[str] = None, timeout: int = 120):
        self._command = build_command(system_prompt, model)
        self._workdir = workdir
        self._timeout = timeout

    def complete(self, prompt: str) -> str:
        try:
            result = subprocess.run(
                self._command, input=prompt, capture_output=True, text=True,
                timeout=self._timeout, cwd=self._workdir,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError(type(exc).__name__) from None
        if result.returncode != 0:
            raise AgentError(f"agent exited with code {result.returncode}")
        return result.stdout.strip()
