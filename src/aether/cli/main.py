"""Aether CLI — main entry point.

`aether` command — start the interactive terminal agent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from aether.core.config import load_config, AetherConfig
from aether.core.llm import ChatMessage
from aether.core.loop import AgentLoop
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
    parser.add_argument("message", nargs="*", default=None, help="Message (non-interactive mode)")
    return parser.parse_args()


async def run_interactive(config: AetherConfig, platform: PlatformInfo) -> None:
    """Run the interactive REPL loop with Agent Loop."""
    console = Console()

    banner = f"""
[bold cyan]Aether[/bold cyan] v0.1.0 — The Universal AI Agent Framework
[dim]Platform:[/dim] {platform.os} | [dim]Shell:[/dim] {platform.shell}
[dim]Model:[/dim]  {config.model.provider}/{config.model.model}
[dim]Workdir:[/dim] {Path.cwd()}
"""
    console.print(Panel(banner.strip(), border_style="cyan"))
    console.print("[dim]/help | /quit | Start chatting or ask me to do things![/dim]\n")

    loop = AgentLoop(config)
    history: list[ChatMessage] = []

    try:
        while True:
            try:
                user_input = console.input("[bold green]❯[/bold green] ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Goodbye![/dim]")
                break

            if not user_input.strip():
                continue

            if user_input.startswith("/"):
                cmd = user_input[1:].strip().lower()
                if cmd in ("quit", "exit", "q"):
                    console.print("[dim]Goodbye![/dim]")
                    break
                elif cmd == "help":
                    console.print("""
[bold]Commands:[/bold]
  /help           Show this help
  /quit, /exit    Exit Aether
  /config         Show configuration
  /platform       Show platform info
  /clear          Clear screen
  /history        Show conversation history
  /tools          List available tools

[bold]Available Tools:[/bold]
  terminal        Execute shell commands
  read_file       Read files with line numbers
  write_file      Write content to files
  search_files    Search with regex
  patch_file      Find-and-replace in files
""")
                elif cmd == "config":
                    console.print(config.model_dump())
                elif cmd == "platform":
                    console.print({
                        "os": platform.os,
                        "shell": platform.shell,
                        "python": platform.python_version,
                        "config_dir": str(platform.config_dir),
                        "data_dir": str(platform.data_dir),
                        "session_id": platform.session_id,
                    })
                elif cmd == "clear":
                    console.clear()
                elif cmd == "history":
                    for i, msg in enumerate(history):
                        role_color = {"user": "green", "assistant": "cyan", "tool": "yellow"}.get(
                            msg.role, "white"
                        )
                        preview = (msg.content or "(tool call)")[:120]
                        console.print(f"[dim]{i}:[/dim] [{role_color}]{msg.role}[/]: {preview}")
                elif cmd == "tools":
                    for name, tool in loop.tools.items():
                        console.print(f"  [bold]{name}[/bold]: {tool.description[:100]}")
                else:
                    console.print(f"[red]Unknown command: /{cmd}[/red]")
                continue

            # User message
            history.append(ChatMessage(role="user", content=user_input))

            # Run agent loop
            console.print()
            full_response = ""

            try:
                async for event in loop.run(
                    user_message=user_input,
                    history=history[:-1],  # exclude the one we just added
                ):
                    if event.type == "thinking":
                        if config.debug:
                            console.print(f"  [dim]🤔 step {event.data.get('step')}...[/dim]")
                    elif event.type == "tool_call":
                        name = event.data.get("name", "?")
                        args = event.data.get("arguments", {})
                        preview = str(args)[:100]
                        console.print(f"  [yellow]🔧 {name}[/yellow] [dim]{preview}[/dim]")
                    elif event.type == "tool_result":
                        name = event.data.get("name", "?")
                        result = event.data.get("result", {})
                        if "error" in result:
                            console.print(f"  [red]✗ {name}: {result['error'][:100]}[/red]")
                        else:
                            preview = str(result)[:150]
                            console.print(f"  [dim]✓ {name} done[/dim]")
                    elif event.type == "text_delta":
                        content = event.data.get("content", "")
                        console.print(content, end="", markup=False, highlight=False)
                        full_response += content
                    elif event.type == "error":
                        console.print(f"\n[red]Error: {event.data.get('message', 'unknown')}[/red]")
                    elif event.type == "text_done":
                        pass  # already printed in text_delta
                    elif event.type == "done":
                        status = event.data.get("status", "?")
                        steps = event.data.get("steps", 0)
                        if config.debug:
                            console.print(f"\n[dim]({status}, {steps} steps)[/dim]")

            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]")
                if config.debug:
                    import traceback
                    traceback.print_exc()
                full_response = f"[Error: {e}]"

            if full_response:
                history.append(ChatMessage(role="assistant", content=full_response))
            console.print("\n")

    finally:
        await loop.close()


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

    platform = PlatformInfo()
    asyncio.run(run_interactive(config, platform))


if __name__ == "__main__":
    main()
