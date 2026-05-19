"""Aether Rich CLI — simple REPL mode (--simple flag).

Kept for lightweight usage and as fallback when Textual isn't available.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from aether.core.config import AetherConfig
from aether.core.llm import ChatMessage
from aether.core.loop import AgentLoop
from aether.platform import PlatformInfo


async def run_rich_cli(config: AetherConfig, workdir: Path) -> None:
    """Simple Rich REPL loop."""
    console = Console()
    platform = PlatformInfo()

    banner = f"""
[bold cyan]Aether[/bold cyan] v0.1.0 — Simple Mode
[dim]Platform:[/dim] {platform.os} | [dim]Model:[/dim] {config.model.provider}/{config.model.model}
"""
    console.print(Panel(banner.strip(), border_style="cyan"))
    console.print("[dim]/help | /quit | Start chatting[/dim]\n")

    loop = AgentLoop(config, workdir)
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
                    console.print("[bold]/help /quit /tools /breakers /clear[/bold]")
                elif cmd == "tools":
                    for name, tool in loop.tools.items():
                        console.print(f"  {name}: {tool.description[:80]}")
                elif cmd == "skills":
                    skills = loop.skills.list_all()
                    console.print(f"[bold]Skills ({len(skills)}):[/bold]")
                    for s in skills:
                        console.print(f"  [bold]{s.name}[/bold] [{s.category}]: {s.description[:60]}")
                elif cmd == "breakers":
                    for s in loop.breakers.status_all():
                        color = "red" if s["state"] == "open" else "green"
                        console.print(f"  {s['name']}: [{color}]{s['state']}[/] (fails: {s['failure_count']})")
                elif cmd == "clear":
                    console.clear()
                continue

            history.append(ChatMessage(role="user", content=user_input))
            console.print()

            try:
                async for event in loop.run(user_message=user_input, history=history[:-1]):
                    if event.type == "tool_call":
                        name = event.data.get("name", "?")
                        console.print(f"  [yellow]🔧 {name}[/yellow]")
                    elif event.type == "text_delta":
                        console.print(event.data.get("content", ""), end="", markup=False)
                    elif event.type == "permission_request":
                        console.print(f"\n  [orange1]⚠ {event.data.get('tool')} needs approval[/orange1]")
                        console.print(f"  [dim]{event.data.get('args', '')[:100]}[/dim]")
                        choice = console.input("  [A]pprove / [D]eny / [S]ession? ").strip().lower()
                        if choice == "a":
                            loop.handle_approval(event.data.get("id"), "approve")
                        elif choice == "s":
                            loop.handle_approval(event.data.get("id"), "approve_session")
                        else:
                            loop.handle_approval(event.data.get("id"), "deny")
                    elif event.type == "error":
                        console.print(f"\n[red]{event.data.get('message', 'unknown')}[/red]")
                    elif event.type == "done":
                        console.print(f"\n[dim]({event.data.get('status', '?')})[/dim]")
            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]")
            console.print("\n")
    finally:
        try:
            await loop.close()
        except Exception:
            pass  # Windows httpx cleanup may fail, ignore
