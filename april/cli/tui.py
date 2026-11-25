"""One Textual TUI for the new runtime."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog


class APRILApp(App):
    CSS = """
    #log { height: 1fr; border: round $accent; }
    #prompt { dock: bottom; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, runtime=None):
        super().__init__()
        self._runtime = runtime
        self.transcript: list[str] = []

    @property
    def runtime(self):
        if self._runtime is None:
            from ..app import build
            from ..config import Settings

            self._runtime = build(Settings.from_env())
        return self._runtime

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", markup=False)
        yield Input(placeholder="Ask APRIL...", id="prompt")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        if not text:
            return
        log = self.query_one("#log", RichLog)
        log.write(f"You: {text}")
        self.transcript.append(f"You: {text}")
        try:
            reply = await asyncio.to_thread(self.runtime.respond, text)
        except Exception as exc:
            reply = f"Error: {exc}"
        log.write(f"APRIL: {reply}")
        self.transcript.append(f"APRIL: {reply}")
