"""Aether Textual TUI — terminal user interface.

Replaces the Rich REPL with a proper Textual-based terminal UI.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)
from textual.binding import Binding
from textual.reactive import reactive

from aether.core.config import AetherConfig
from aether.core.loop import AgentLoop
from aether.core.llm import ChatMessage


class ChatMessageWidget(Static):
    """A single chat message bubble."""

    def __init__(self, role: str, content: str):
        super().__init__("")
        self.role = role
        self.content = content

    def compose(self) -> ComposeResult:
        prefix = {"user": "❯", "assistant": "●", "tool": "🔧", "error": "✗"}
        color = {"user": "green", "assistant": "cyan", "tool": "yellow", "error": "red"}
        p = prefix.get(self.role, "·")
        c = color.get(self.role, "white")
        yield Static(f"[bold {c}]{p}[/bold {c}] {self.content}", id=f"msg-{id(self)}")


class ToolCallWidget(Static):
    """Tool call indicator."""

    def __init__(self, tool_name: str, args: str):
        super().__init__("")
        self.tool_name = tool_name
        self.args = args

    def compose(self) -> ComposeResult:
        yield Static(f"  [yellow]🔧 {self.tool_name}[/yellow] [dim]{self.args[:80]}[/dim]")


class AetherTUI(App):
    """Main Textual TUI for Aether."""

    CSS = """
    Screen {
        background: #0d1117;
    }

    #chat-area {
        height: 1fr;
        border: solid #30363d;
        background: #0d1117;
        padding: 0 1;
    }

    #input-area {
        height: auto;
        border: solid #30363d;
        background: #161b22;
        padding: 1;
    }

    #input {
        width: 1fr;
    }

    #input:focus {
        border: solid #58a6ff;
    }

    #status-bar {
        height: 1;
        background: #161b22;
        color: #8b949e;
        padding: 0 1;
    }

    #send-btn {
        width: 8;
        margin-left: 1;
    }

    RichLog {
        background: #0d1117;
        color: #c9d1d9;
        border: none;
    }

    .user-msg {
        color: #7ee787;
    }

    .assistant-msg {
        color: #c9d1d9;
    }

    .tool-msg {
        color: #d2a8ff;
    }

    .error-msg {
        color: #f85149;
    }

    .thinking {
        color: #8b949e;
        text-style: italic;
    }

    .approval-panel {
        background: #1f2937;
        border: solid #f0883e;
        padding: 1;
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("escape", "focus_input", "Focus Input"),
    ]

    def __init__(self, config: AetherConfig, workdir: Path | None = None):
        super().__init__()
        self.config = config
        self.workdir = workdir or Path.cwd()
        self._loop: AgentLoop | None = None
        self._history: list[ChatMessage] = []
        self._running = False
        self._pending_approval_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat-area", highlight=True, markup=True, wrap=True)
        yield Static("", id="approval-panel")
        with Horizontal(id="input-area"):
            yield Input(placeholder="Type a message or /command...", id="input")
            yield Button("Send", id="send-btn", variant="primary")
        yield Static(
            f"Model: {self.config.model.provider}/{self.config.model.model} | "
            f"Workdir: {self.workdir} | Ctrl+Q to quit",
            id="status-bar",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize agent loop on mount."""
        self._loop = AgentLoop(self.config, self.workdir)
        self._running = True
        self.query_one("#chat-area", RichLog).write(
            "[bold cyan]Aether[/bold cyan] v0.1.0 — Ready\n"
            f"[dim]Model: {self.config.model.provider}/{self.config.model.model}[/dim]\n"
            f"[dim]Workdir: {self.workdir}[/dim]\n"
        )

    def on_unmount(self) -> None:
        """Cleanup on unmount."""
        if self._loop:
            asyncio.create_task(self._loop.close())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._send_message()
        elif event.button.id == "approve-btn":
            self._handle_approval("approve")
        elif event.button.id == "approve-session-btn":
            self._handle_approval("approve_session")
        elif event.button.id == "deny-btn":
            self._handle_approval("deny")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._send_message()

    async def _send_message(self) -> None:
        """Send a user message and run the agent loop."""
        if not self._running:
            return

        inp = self.query_one("#input", Input)
        text = inp.value.strip()
        if not text:
            return

        inp.value = ""
        log = self.query_one("#chat-area", RichLog)

        # Commands
        if text.startswith("/"):
            await self._handle_command(text, log)
            return

        # User message
        log.write(f"\n[bold green]❯[/bold green] {text}\n")
        self._history.append(ChatMessage(role="user", content=text))

        # Run agent loop
        try:
            async for event in self._loop.run(
                user_message=text,
                history=self._history[:-1],
            ):
                if event.type == "tool_call":
                    name = event.data.get("name", "?")
                    args = str(event.data.get("arguments", {}))[:100]
                    log.write(f"  [yellow]🔧 {name}[/yellow] [dim]{args}[/dim]")

                elif event.type == "tool_result":
                    name = event.data.get("name", "?")
                    result = event.data.get("result", {})
                    if "error" in result:
                        log.write(f"  [red]✗ {name} failed[/red]")
                    else:
                        preview = str(result)[:120]
                        log.write(f"  [dim]✓ {name}[/dim]")

                elif event.type == "text_delta":
                    content = event.data.get("content", "")
                    log.write(content)

                elif event.type == "permission_request":
                    self._pending_approval_id = event.data.get("id")
                    await self._show_approval_panel(event.data, log)

                elif event.type == "error":
                    log.write(f"[red]Error: {event.data.get('message', 'unknown')}[/red]")

                elif event.type == "done":
                    status = event.data.get("status", "?")
                    log.write(f"[dim]({status})[/dim]\n")

        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

        # Poll pending approvals
        if self._loop:
            pending = self._loop.get_pending_approvals()
            if pending:
                await self._show_approval_panel(pending[0], log)

    async def _handle_command(self, text: str, log: RichLog) -> None:
        """Handle slash commands."""
        cmd = text[1:].strip().lower()
        if cmd in ("q", "quit", "exit"):
            self._running = False
            await self._loop.close()
            self.exit()
        elif cmd == "clear":
            log.clear()
        elif cmd == "help":
            log.write("""
[bold]Commands:[/bold]
  /help          Show help
  /quit, /q      Exit
  /clear         Clear chat
  /tools         List tools
  /config        Show config
  /breakers      Circuit breaker status
""")
        elif cmd == "tools":
            if self._loop:
                for name, tool in self._loop.tools.items():
                    log.write(f"  [bold]{name}[/bold]: {tool.description[:100]}")
        elif cmd == "config":
            log.write(str(self.config.model_dump()))
        elif cmd == "breakers":
            if self._loop:
                for status in self._loop.breakers.status_all():
                    log.write(f"  {status['name']}: [{'red' if status['state'] == 'open' else 'green'}]{status['state']}[/] "
                              f"(failures: {status['failure_count']})")
        elif cmd == "memory":
            if self._loop:
                stats = self._loop.memory.stats()
                log.write(f"[bold]Memory:[/bold] {stats['total_entries']}/{stats['max_entries']} "
                          f"({stats['usage_percent']}%) "
                          f"user:{stats['by_target'].get('user',0)} "
                          f"project:{stats['by_target'].get('project',0)} "
                          f"env:{stats['by_target'].get('environment',0)}")
                recent = self._loop.memory.recall_recent(5)
                if recent:
                    log.write("\n[bold]Recent:[/bold]")
                    for r in recent:
                        log.write(f"  [dim]{r.target}[/dim]: {r.content[:80]}")
        elif cmd == "skills":
            if self._loop:
                all_skills = self._loop.skills.list_all()
                log.write(f"[bold]Skills ({len(all_skills)}):[/bold]")
                for s in all_skills:
                    triggers = ", ".join(s.triggers[:3])
                    log.write(f"  [bold]{s.name}[/bold] [{s.category}] {s.description[:60]}")
                    log.write(f"    [dim]triggers: {triggers}[/dim]")
        elif cmd.startswith("skill "):
            if self._loop:
                name = text[6:].strip()  # After "/skill "
                skill = self._loop.skills.load(name)
                if skill:
                    log.write(f"[bold]{skill.meta.name}[/bold] v{skill.meta.version}")
                    log.write(f"[dim]{skill.body[:500]}[/dim]")
                else:
                    log.write(f"[red]Skill not found: {name}[/red]")
        elif cmd.startswith("remember "):
            if self._loop:
                fact = text[9:].strip()  # After "/remember "
                self._loop.memory.remember(fact, target="user")
                log.write(f"[green]✓ Remembered:[/green] {fact[:80]}")
        elif cmd.startswith("forget "):
            if self._loop:
                mem_id = text[7:].strip()
                if self._loop.memory.forget(mem_id):
                    log.write(f"[green]✓ Forgotten:[/green] {mem_id}")
                else:
                    log.write(f"[red]Not found:[/red] {mem_id}")
        else:
            log.write(f"[red]Unknown: {text}[/red]")

    async def _show_approval_panel(self, data: dict, log: RichLog) -> None:
        """Show approval request UI."""
        panel = self.query_one("#approval-panel", Static)
        panel.update(
            f"[bold orange1]⚠ Approval Required[/bold orange1]\n"
            f"Tool: [bold]{data.get('tool', '?')}[/bold]\n"
            f"Risk: {data.get('risk', '?')}\n"
            f"Args: [dim]{data.get('args', '')[:100]}[/dim]\n\n"
            f"[green]A[/green]pprove  [green]S[/green]ession  [red]D[/red]eny  (press key)"
        )
        self._pending_approval_id = data.get("id")

        # Bind approval keys
        self.bind("a", "approve", "Approve")
        self.bind("s", "approve_session", "Approve Session")
        self.bind("d", "deny", "Deny")

    def action_approve(self) -> None:
        self._handle_approval("approve")

    def action_approve_session(self) -> None:
        self._handle_approval("approve_session")

    def action_deny(self) -> None:
        self._handle_approval("deny")

    def action_focus_input(self) -> None:
        self.query_one("#input", Input).focus()

    def action_clear(self) -> None:
        self.query_one("#chat-area", RichLog).clear()

    async def _handle_approval(self, decision: str) -> None:
        """Process approval decision and continue."""
        if not self._loop or not self._pending_approval_id:
            return

        log = self.query_one("#chat-area", RichLog)
        panel = self.query_one("#approval-panel", Static)

        self._loop.handle_approval(self._pending_approval_id, decision)
        panel.update("")
        self._pending_approval_id = None

        log.write(f"[dim]({decision})[/dim]")

        # Resume the loop
        try:
            async for event in self._loop.run(
                user_message="",
                history=self._history,
            ):
                if event.type == "text_delta":
                    log.write(event.data.get("content", ""))
                elif event.type == "done":
                    log.write(f"[dim]({event.data.get('status', '?')})[/dim]\n")
                elif event.type == "tool_call":
                    log.write(f"  [yellow]🔧 {event.data.get('name', '?')}[/yellow]")
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    async def action_quit(self) -> None:
        self._running = False
        if self._loop:
            await self._loop.close()
        self.exit()


def run_tui(config: AetherConfig, workdir: Path | None = None) -> None:
    """Launch the Textual TUI."""
    app = AetherTUI(config, workdir)
    app.run()
