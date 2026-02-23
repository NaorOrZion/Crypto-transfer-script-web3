"""
Entry point for the MEV micro-farming bot.

Usage:
    python -m mev_bot
"""

import asyncio

from .watcher import run


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
