"""Aether Rich CLI — simple REPL mode (--simple flag)."""

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
                    console.print("[bold]/help /quit /tools /skills /memory /breakers /clear[/bold]")
                elif cmd == "tools":
                    for name, tool in loop.tools.items():
                        console.print(f"  [bold]{name}[/bold]: {tool.description[:80]}")
                elif cmd == "skills":
                    skills = loop.skills.list_all()
                    console.print(f"[bold]Skills ({len(skills)}):[/bold]")
                    for s in skills:
                        console.print(f"  [bold]{s.name}[/bold] [{s.category}]: {s.description[:60]}")
                elif cmd == "memory":
                    stats = loop.memory.stats()
                    console.print(f"[bold]Memory:[/bold] {stats['total_entries']}/{stats['max_entries']} ({stats['usage_percent']}%)")
                elif cmd == "breakers":
                    for s in loop.breakers.status_all():
                        color = "red" if s["state"] == "open" else "green"
                        console.print(f"  {s['name']}: [{color}]{s['state']}[/] (fails: {s['failure_count']})")
                elif cmd == "clear":
                    console.clear()
                continue

            # ── Run agent loop for user message ──
            history.append(ChatMessage(role="user", content=user_input))

            # We may need multiple loop runs if approval is needed
            messages_for_loop = list(history[:-1])  # exclude latest user msg (loop adds it)
            user_msg = user_input

            while True:
                console.print()
                try:
                    async for event in loop.run(user_message=user_msg, history=messages_for_loop):
                        if event.type == "tool_call":
                            name = event.data.get("name", "?")
                            console.print(f"  [yellow]🔧 {name}[/yellow]")

                        elif event.type == "tool_result":
                            name = event.data.get("name", "?")
                            result = event.data.get("result", {})
                            if "error" in result:
                                console.print(f"  [red]✗ {name}: {result['error'][:100]}[/red]")
                            elif "output" in result:
                                out = result["output"].strip()[:300]
                                if out:
                                    console.print(f"  [dim]{out}[/dim]")

                        elif event.type == "text_delta":
                            content = event.data.get("content", "")
                            console.print(content, end="", markup=False)

                        elif event.type == "permission_request":
                            tool_name = event.data.get("tool", "?")
                            args = event.data.get("args", "")[:150]
                            console.print(f"\n  [orange1]⚠ {tool_name} needs approval[/orange1]")
                            console.print(f"  [dim]{args}[/dim]")
                            choice = console.input("  [A]pprove / [D]eny / [S]ession? ").strip().lower()

                            req_id = event.data.get("id")
                            if choice == "a":
                                loop.handle_approval(req_id, "approve")
                            elif choice == "s":
                                loop.handle_approval(req_id, "approve_session")
                            else:
                                loop.handle_approval(req_id, "deny")

                            # Restart loop from current state (no new user msg)
                            user_msg = ""
                            messages_for_loop = None  # loop will use internal state
                            break  # break inner for, restart while

                        elif event.type == "error":
                            console.print(f"\n[red]{event.data.get('message', 'unknown')[:200]}[/red]")

                        elif event.type == "done":
                            status = event.data.get("status", "?")
                            console.print(f"\n[dim]({status})[/dim]")
                            user_msg = "__DONE__"  # signal to exit inner while

                    else:
                        # for-loop completed without break → done
                        user_msg = "__DONE__"

                except Exception as e:
                    console.print(f"\n[red]Error: {e}[/red]")
                    user_msg = "__DONE__"

                if user_msg == "__DONE__":
                    console.print()
                    break  # exit inner while, back to user input

    finally:
        try:
            await loop.close()
        except Exception:
            pass
