import asyncio
from pathlib import Path

import pytest

from april.body import FakeDevices
from april.cli.tui import APRILApp
from april.events import EventBus
from april.llm import ChatReply, FakeModel
from april.memory import InMemoryMemoryStore
from april.persona import Persona
from april.runtime import Runtime
from april.tools import BuiltinSource, Toolset


def _runtime(tmp_path: Path) -> Runtime:
    return Runtime(
        FakeModel([ChatReply(text="hello")]),
        "test",
        InMemoryMemoryStore(),
        Persona(
            name="test",
            system_text="You are a test.",
            guardrails="",
            temperature=0.2,
            source="test",
        ),
        Toolset([BuiltinSource(workspace=tmp_path)]),
        EventBus(),
        FakeDevices(),
    )


@pytest.mark.asyncio
async def test_tui_mounts_and_replies(tmp_path):
    app = APRILApp(runtime=_runtime(tmp_path))
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        prompt.focus()
        prompt.value = "hi"
        await pilot.press("enter")
        for _ in range(40):
            if app.transcript:
                break
            await asyncio.sleep(0.05)
        assert "APRIL: hello" in app.transcript
