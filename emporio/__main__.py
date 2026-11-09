"""Chat with the agent in the terminal: python -m emporio [--today YYYY-MM-DD]"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from emporio.agent import Agent
from emporio.store import Store

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
EXIT_WORDS = {"sair", "exit", "quit"}


def load_env(path: Path = ENV_FILE) -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Empório da Música customer service agent")
    parser.add_argument("--today", type=date.fromisoformat,
                        help="reference date for order checks such as return windows (default: today)")
    args = parser.parse_args()
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is missing: copy .env.example to .env and fill it in.")

    agent = Agent(Store(today=args.today))
    piped = not sys.stdin.isatty()
    print("Empório da Música: atendimento virtual. Digite 'sair' para encerrar.")
    try:
        while True:
            text = input("\nVocê: ").strip()
            if piped:
                print(text)  # echo scripted input so the output reads as a transcript
            if text.lower() in EXIT_WORDS:
                break
            if text:
                print(f"\nDuda: {agent.reply(text)}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        agent.close()
        print(f"\n[log: {agent.log.path}]")


if __name__ == "__main__":
    main()
