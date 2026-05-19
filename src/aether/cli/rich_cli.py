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

    # ── Banner (static Rich markup) ──
    console.print(Panel(
        f"[bold cyan]Aether[/bold cyan] v0.2.1\n"
        f"[dim]{platform.os} · {config.model.provider}/{config.model.model}[/dim]",
        border_style="cyan",
    ))
    console.print("[dim]输入消息开始对话 · /help 查看命令 · /quit 退出[/dim]\n")

    loop = AgentLoop(config, workdir)
    history: list[ChatMessage] = []

    try:
        while True:
            try:
                user_input = console.input("[bold green]▸[/bold green] ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]再见~[/dim]")
                break

            if not user_input.strip():
                continue

            if user_input.startswith("/"):
                _handle_command(user_input, loop, console)
                continue

            # ── Agent loop ──
            history.append(ChatMessage(role="user", content=user_input))
            messages_for_loop = list(history[:-1])
            user_msg = user_input

            while True:
                console.print()
                try:
                    async for event in loop.run(user_message=user_msg, history=messages_for_loop):

                        if event.type == "thinking":
                            pass

                        elif event.type == "tool_call":
                            name = str(event.data.get("name", "?"))
                            cmd = str(event.data.get("arguments", {}).get("command", ""))[:80]
                            # Use markup=False for ALL dynamic content
                            console.print(f"  ⚡ {name}", markup=False)
                            if cmd:
                                console.print(f"    {cmd}", style="dim", markup=False)

                        elif event.type == "tool_result":
                            name = str(event.data.get("name", "?"))
                            result = event.data.get("result", {})
                            if "error" in result:
                                console.print(f"  ✗ {name}: {str(result['error'])[:150]}", style="red", markup=False)
                            elif "output" in result:
                                out = str(result["output"]).strip()
                                if out:
                                    for line in out.split("\n")[:15]:
                                        console.print(f"  │ {line[:120]}", style="dim", markup=False)

                        elif event.type == "text_delta":
                            content = str(event.data.get("content", ""))
                            console.print(content, markup=False, highlight=False)

                        elif event.type == "permission_request":
                            tool_name = str(event.data.get("tool", "?"))
                            console.print(f"\n  ⚠ {tool_name} 需要授权", style="bold orange1", markup=False)
                            console.print(f"  {str(event.data.get('args', ''))[:200]}", style="dim", markup=False)
                            choice = console.input("  [A]通过 [D]拒绝 [S]本次会话允许? ").strip().lower()

                            req_id = str(event.data.get("id", ""))
                            if choice == "s":
                                loop.handle_approval(req_id, "approve_session")
                            elif choice == "a":
                                loop.handle_approval(req_id, "approve")
                            else:
                                loop.handle_approval(req_id, "deny")

                            user_msg = ""
                            messages_for_loop = None
                            break

                        elif event.type == "error":
                            console.print(f"  Error: {str(event.data.get('message', '?'))[:200]}", style="red", markup=False)

                        elif event.type == "done":
                            status = str(event.data.get("status", "?"))
                            steps = event.data.get("steps", 0)
                            console.print(f"  ({status}, {steps} steps)", style="dim", markup=False)
                            user_msg = "__DONE__"

                    else:
                        user_msg = "__DONE__"

                except Exception as e:
                    console.print(f"  Error: {e}", style="red", markup=False)
                    user_msg = "__DONE__"

                if user_msg == "__DONE__":
                    console.print()
                    break

    finally:
        try:
            await loop.close()
        except BaseException:
            pass


def _handle_command(text: str, loop: AgentLoop, console: Console) -> None:
    cmd = text[1:].strip().lower()
    if cmd in ("q", "quit", "exit"):
        console.print("[dim]再见~[/dim]")
        raise KeyboardInterrupt()
    elif cmd == "help":
        # Static Rich markup — safe
        console.print("""
[bold]命令[/bold]
  [green]/help[/green]      帮助
  [green]/quit[/green]      退出
  [green]/tools[/green]     可用工具
  [green]/skills[/green]    已加载技能
  [green]/memory[/green]    记忆统计
  [green]/breakers[/green]  断路保护
  [green]/clear[/green]     清屏
""")
    elif cmd == "tools":
        for name, tool in loop.tools.items():
            console.print(f"  [bold yellow]{name}[/bold yellow]: {tool.description[:100]}")
    elif cmd == "skills":
        skills = loop.skills.list_all()
        console.print(f"[bold]Skills ({len(skills)}):[/bold]")
        for s in skills:
            console.print(f"  [bold]{s.name}[/bold] [{s.category}] {s.description[:80]}")
    elif cmd == "memory":
        stats = loop.memory.stats()
        console.print(f"[bold]Memory:[/bold] {stats['total_entries']}/{stats['max_entries']} ({stats['usage_percent']}%)")
    elif cmd == "breakers":
        for s in loop.breakers.status_all():
            color = "red" if s["state"] == "open" else "green"
            console.print(f"  {s['name']}: [{color}]{s['state']}[/]")
    elif cmd == "clear":
        console.clear()
    else:
        console.print(f"[red]未知命令: {text}[/red]")
