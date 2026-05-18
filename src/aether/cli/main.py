"""Aether CLI — main entry point.

`aether` command → Textual TUI (default) or Rich REPL (--simple).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from aether.core.config import load_config, AetherConfig
from aether.platform import PlatformInfo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aether",
        description="Aether — The Universal AI Agent Framework",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config file")
    parser.add_argument("--model", "-m", type=str, default=None, help="Model (provider/model)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--workdir", "-w", type=str, default=".", help="Working directory")
    parser.add_argument("--simple", action="store_true", help="Use simple Rich REPL (no TUI)")
    parser.add_argument("message", nargs="*", default=None, help="Message (non-interactive)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.version:
        from aether import __version__
        print(f"Aether v{__version__}")
        return

    config = load_config(args.config)
    if args.debug:
        config.debug = True
    if args.model:
        parts = args.model.split("/", 1)
        if len(parts) == 2:
            config.model.provider = parts[0]
            config.model.model = parts[1]
        else:
            config.model.model = args.model

    workdir = Path(args.workdir).resolve()

    if args.simple:
        # Rich REPL mode
        from aether.cli.rich_cli import run_rich_cli
        asyncio.run(run_rich_cli(config, workdir))
    else:
        # Textual TUI mode (default)
        from aether.cli.tui import run_tui
        run_tui(config, workdir)


if __name__ == "__main__":
    main()
