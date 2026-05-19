"""Aether Rich CLI — simple REPL mode (--simple flag).

Optimized with Claude Code / Codex / Hermes best practices:
- Markdown rendering for responses
- Rich thinking/progress indicators  
- Clean tool execution banners
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel

from aether.core.config import AetherConfig
from aether.core.llm import ChatMessage
from aether.core.loop import AgentLoop
from aether.platform import PlatformInfo


async def run_rich_cli(config: AetherConfig, workdir: Path) -> None:
    console = Console()
    platform = PlatformInfo()

    banner = Panel(
        f"[bold cyan]Aether[/bold cyan] v0.2.0\n"
        f"[dim]{platform.os} · {config.model.provider}/{config.model.model}[/dim]",
        border_style="cyan",
    )
    console.print(banner)
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

            # Commands
            if user_input.startswith("/"):
                await _handle_command(user_input, loop, console)
                continue

            # ── Agent loop with continuation support ──
            history.append(ChatMessage(role="user", content=user_input))
            messages_for_loop = list(history[:-1])
            user_msg = user_input

            while True:
                console.print()
                try:
                    async for event in loop.run(user_message=user_msg, history=messages_for_loop):
                        if event.type == "thinking":
                            pass  # skip thinking indicator to avoid Rich \r issues

                        elif event.type == "tool_call":
                            name = event.data.get("name", "?")
                            args = event.data.get("arguments", {})
                            cmd_preview = rich_escape(str(args.get("command", str(args)))[:80])
                            console.print(f"  [yellow]⚡ {name}[/yellow] [dim]{cmd_preview}[/dim]")

                        elif event.type == "tool_result":
                            name = event.data.get("name", "?")
                            result = event.data.get("result", {})
                            if "error" in result:
                                err = rich_escape(str(result["error"])[:100])
                                console.print(f"  [red]✗ {name}[/red] [dim]{err}[/dim]")
                            elif "output" in result:
                                out = result["output"].strip()
                                if out:
                                    for line in out.split("\n")[:10]:
                                        console.print(f"  [dim]│ {rich_escape(line[:100])}[/dim]")

                        elif event.type == "text_delta":
                            content = event.data.get("content", "")
                            console.print(content, markup=False, highlight=False)

                        elif event.type == "permission_request":
                            tool_name = event.data.get("tool", "?")
                            args = rich_escape(str(event.data.get("args", ""))[:120])
                            risk = rich_escape(str(event.data.get("risk", "")))
                            console.print(Panel(
                                f"[bold orange1]🔐 需要授权[/bold orange1]\n"
                                f"[bold]{tool_name}[/bold]\n"
                                f"[dim]{risk}[/dim]\n"
                                f"[dim italic]{args}[/dim]",
                                border_style="orange1",
                            ))
                            choice = console.input("  [[green]A[/green]]通过 [[red]D[/red]]拒绝 [[green]S[/green]]本次会话始终允许? ").strip().lower()

                            req_id = event.data.get("id")
                            if choice == "s":
                                loop.handle_approval(req_id, "approve_session")
                                console.print("  [green]✓ 本次会话自动允许[/green]")
                            elif choice == "a":
                                loop.handle_approval(req_id, "approve")
                            else:
                                loop.handle_approval(req_id, "deny")
                                console.print("  [red]✗ 已拒绝[/red]")

                            user_msg = ""
                            messages_for_loop = None
                            break

                        elif event.type == "error":
                            msg = rich_escape(str(event.data.get('message', '?'))[:200])
                            console.print(f"\n[red]Error: {msg}[/red]")

                        elif event.type == "done":
                            status = event.data.get("status", "?")
                            steps = event.data.get("steps", 0)
                            console.print(f"[dim]({status}, {steps} steps)[/dim]")
                            user_msg = "__DONE__"

                    else:
                        user_msg = "__DONE__"

                except Exception as e:
                    console.print(f"\n[red]Error: {rich_escape(str(e))}[/red]")
                    user_msg = "__DONE__"

                if user_msg == "__DONE__":
                    console.print()
                    break

    finally:
        try:
            await loop.close()
        except BaseException:
            pass


async def _handle_command(text: str, loop: AgentLoop, console: Console) -> None:
    cmd = text[1:].strip().lower()
    if cmd in ("q", "quit", "exit"):
        console.print("[dim]再见~[/dim]")
        raise KeyboardInterrupt()
    elif cmd == "help":
        console.print("""
[bold]命令[/bold]
  [green]/help[/green]      帮助
  [green]/quit[/green]      退出
  [green]/tools[/green]     可用工具
  [green]/skills[/green]    已加载技能
  [green]/memory[/green]    记忆统计
  [green]/breakers[/green]  断路保护状态
  [green]/clear[/green]     清屏

[bold]工具（Agent 可自主调用）[/bold]
  [yellow]terminal[/yellow]   执行 Shell 命令
  [yellow]read_file[/yellow]  读取文件
  [yellow]write_file[/yellow] 写入文件
  [yellow]search_files[/yellow] 搜索文件
  [yellow]patch_file[/yellow]  查找替换

[bold]提示[/bold]
  授权时按 [green]S[/green] 可让本次会话不再询问
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
