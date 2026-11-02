"""Start the assistant. Settings come from the environment."""

import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from .agent import ClaudeAgent, Prompts
from .assistant import Assistant
from .bot import Bot, BotApi
from .knowledge import KnowledgeBase
from .store import Store


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Stops HTTP client logs from printing request URLs, which contain the bot token.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing required setting: TELEGRAM_BOT_TOKEN")
    data_dir = Path(os.environ.get("ASSISTANT_DATA_DIR", "private")).expanduser().resolve()
    agent_dir = data_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    prompts = Prompts(data_dir / "prompts")
    store = Store(data_dir / "assistant.sqlite3")
    assistant = Assistant(
        store, KnowledgeBase.load(data_dir / "knowledge.md"), prompts,
        ClaudeAgent(prompts.system, agent_dir, os.environ.get("CLAUDE_MODEL", "").strip() or None),
    )
    bot = Bot(BotApi(token), assistant, store, follow_ups=os.environ.get("ENABLE_FOLLOW_UPS") == "1")

    def stop(*_):
        bot.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bot.run_forever()


if __name__ == "__main__":
    main()
