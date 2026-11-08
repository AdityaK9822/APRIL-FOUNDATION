"""Tests for the unified FeatureAdapter runtime (Phase 2).

These tests drive ``AgentRuntime.run`` with a fake OpenAI client so the full
request/response pipeline is exercised offline: feature pre-call text must land
in the request messages, tool dispatch must respect first ownership, and
post-call hooks must fire after the final assistant response.
"""

import sys
from pathlib import Path

import pytest

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import AgentRuntime
from core.config import AgentConfig
from core.events import reset_event_bus
from core.feature_adapter import NoopFeatureAdapter


class FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message: FakeMessage):
        self.message = message


class FakeResponse:
    def __init__(self, message: FakeMessage):
        self.choices = [FakeChoice(message)]


class FakeCompletions:
    """Records every request and replays queued responses (or callables)."""

    def __init__(self, responses):
        self.requests = []
        self.responses = list(responses)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        return response(kwargs) if callable(response) else response


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = FakeChat(completions)


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


@pytest.fixture(autouse=True)
def clean_runtime_state(monkeypatch):
    """Keep persona boot and event subscribers from leaking between tests."""
    monkeypatch.delenv("APRIL_PERSONA", raising=False)
    reset_event_bus()
    yield
    reset_event_bus()


@pytest.fixture
def agent() -> AgentRuntime:
    config = AgentConfig(
        model="test-model",
        api_key="test-key",
        api_base_url="http://localhost:1/v1",
        memory_enabled=False,
    )
    return AgentRuntime(config)


def test_name_question_sees_april_identity(agent):
    """"What's your name?" is answered from the identity system message."""

    def answer(request):
        system_text = "\n".join(
            message.get("content", "")
            for message in request["messages"]
            if message["role"] == "system"
        )
        content = "I am APRIL." if "APRIL" in system_text else "I do not know."
        return FakeResponse(FakeMessage(content=content))

    agent.client = FakeClient(FakeCompletions([answer]))

    assert "APRIL" in agent.run("What's your name?")

    request = agent.client.chat.completions.requests[0]
    assert any(
        message["role"] == "system" and "APRIL" in message["content"]
        for message in request["messages"]
    )


def test_feature_pre_call_text_reaches_request_without_mutating_session(agent):
    class ContextFeature(NoopFeatureAdapter):
        def __init__(self):
            super().__init__("fake-context")
            self.pre_calls = 0

        def pre_call(self, agent, user_input):
            self.pre_calls += 1
            return "FAKE_FEATURE_TEXT"

    feature = ContextFeature()
    agent.features.append(feature)
    agent.client = FakeClient(FakeCompletions([FakeResponse(FakeMessage(content="ok"))]))

    assert agent.run("hello") == "ok"

    request = agent.client.chat.completions.requests[0]
    systems = [
        message for message in request["messages"] if message["role"] == "system"
    ]
    assert any("FAKE_FEATURE_TEXT" in message["content"] for message in systems)
    assert feature.pre_calls == 1

    # The feature text is ephemeral: session history stays conversation-only.
    assert [row["role"] for row in agent.session.context] == ["user", "assistant"]
    assert all(
        "FAKE_FEATURE_TEXT" not in str(row.get("content", ""))
        for row in agent.session.context
    )


def test_dispatch_first_ownership_wins(agent):
    class ClaimingFeature(NoopFeatureAdapter):
        def __init__(self, name: str, result: str):
            super().__init__(name)
            self.result = result
            self.calls = []

        def dispatch(self, tool_name, args, agent):
            self.calls.append((tool_name, args))
            return self.result

    first = ClaimingFeature("first", "first-result")
    second = ClaimingFeature("second", "second-result")
    agent.features.append(first)
    agent.features.append(second)

    assert agent.features.dispatch("shared_tool", {"x": 1}, agent) == "first-result"
    assert first.calls == [("shared_tool", {"x": 1})]
    assert second.calls == []


def test_tool_dispatch_and_post_call_hooks_fire_in_agent_loop(agent):
    class ToolFeature(NoopFeatureAdapter):
        def __init__(self):
            super().__init__("fake-tool")
            self.calls = []
            self.post_calls = []

        def tool_schemas(self, agent):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "fake_tool",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def dispatch(self, tool_name, args, agent):
            self.calls.append((tool_name, args))
            return "tool-result"

        def post_call(self, agent, user_input, assistant_response):
            self.post_calls.append((user_input, assistant_response))

    feature = ToolFeature()
    agent.features.append(feature)

    completions = FakeCompletions(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("call_1", "fake_tool", '{"x": 1}')]
                )
            ),
            FakeResponse(FakeMessage(content="done")),
        ]
    )
    agent.client = FakeClient(completions)

    assert agent.run("use the tool") == "done"

    # Dispatch went to the feature, and post_call ran with the final response.
    assert feature.calls == [("fake_tool", {"x": 1})]
    assert feature.post_calls == [("use the tool", "done")]

    first_request, second_request = completions.requests
    tool_names = [schema["function"]["name"] for schema in first_request["tools"]]
    assert "shell_execute" in tool_names
    assert "fake_tool" in tool_names
    assert any(
        message["role"] == "tool" and message["content"] == "tool-result"
        for message in second_request["messages"]
    )
    assert [row["role"] for row in agent.session.context] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
