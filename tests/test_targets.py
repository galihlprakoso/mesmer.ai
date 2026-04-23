"""Unit tests for target adapters — focused on the `user_turn_suffix` contract.

Every adapter must apply ``Target._apply_suffix`` to every outgoing user message.
Used by the bench runner to sandwich each attacker turn with a per-turn defence
suffix (e.g. Tensor Trust's ``post_prompt``). A missing application on any one
adapter silently voids defences at runtime, so each adapter gets its own test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mesmer.targets import create_target
from mesmer.targets.base import Target
from mesmer.targets.echo import EchoTarget
from mesmer.targets.openai_compat import OpenAITarget
from mesmer.targets.rest import RESTTarget
from mesmer.targets.websocket_target import WebSocketTarget


# ---------------------------------------------------------------------------
# Base class helper — the shared logic every adapter relies on.
# ---------------------------------------------------------------------------


class _DummyTarget(Target):
    """Concrete-enough Target subclass to exercise the base helper directly."""

    async def send(self, message: str) -> str:  # pragma: no cover — not called
        return ""

    async def reset(self) -> None:  # pragma: no cover
        ...

    def get_history(self):  # pragma: no cover
        return []


class TestApplySuffix:
    def test_noop_when_suffix_empty(self):
        t = _DummyTarget()
        t.user_turn_suffix = ""
        assert t._apply_suffix("hello") == "hello"

    def test_appends_with_newline_when_suffix_set(self):
        t = _DummyTarget()
        t.user_turn_suffix = "POST"
        assert t._apply_suffix("attacker text") == "attacker text\nPOST"

    def test_preserves_input_when_target_never_configured(self):
        """Default-constructed targets (no suffix arg) behave identically to
        pre-suffix code paths — guards every non-bench scenario."""
        t = _DummyTarget()
        assert t._apply_suffix("anything") == "anything"


# ---------------------------------------------------------------------------
# EchoTarget — trivial to test, no mocking needed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEchoTargetSuffix:
    async def test_send_wraps_with_suffix(self):
        t = EchoTarget(user_turn_suffix="POST")
        reply = await t.send("HELLO")
        # Echo returns "Echo: {wrapped}" — confirms both that wrap happened
        # and that history captured the wrapped text.
        assert reply == "Echo: HELLO\nPOST"
        assert t.get_history()[0].sent == "HELLO\nPOST"

    async def test_send_is_unchanged_when_no_suffix(self):
        t = EchoTarget()
        reply = await t.send("HELLO")
        assert reply == "Echo: HELLO"
        assert t.get_history()[0].sent == "HELLO"


# ---------------------------------------------------------------------------
# OpenAITarget — mock the openai client; verify the user message dict content.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenAITargetSuffix:
    async def test_user_message_carries_suffix_into_api_call(self):
        target = OpenAITarget(
            base_url="http://unused",
            model="m",
            api_key="k",
            system_prompt="SYS",
            user_turn_suffix="POST",
        )

        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="reply"))],
            usage=None,
        )
        create_mock = AsyncMock(return_value=fake_response)
        target._client.chat.completions.create = create_mock  # type: ignore[attr-defined]

        reply = await target.send("ATTACK")

        assert reply == "reply"
        # Find the user message in the outgoing messages arg.
        call_kwargs = create_mock.call_args.kwargs
        user_msgs = [m for m in call_kwargs["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "ATTACK\nPOST"
        # System prompt stays as-is — it's pre_prompt, not pre+post.
        sys_msgs = [m for m in call_kwargs["messages"] if m["role"] == "system"]
        assert sys_msgs == [{"role": "system", "content": "SYS"}]


# ---------------------------------------------------------------------------
# RESTTarget — mock httpx; verify the body template was substituted with
# the wrapped message.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRESTTargetSuffix:
    async def test_body_template_renders_wrapped_message(self):
        target = RESTTarget(
            url="http://unused",
            body_template='{"input": "{{message}}"}',
            response_path="reply",
            user_turn_suffix="POST",
        )

        captured: dict = {}

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"reply": "ok"}

        class _FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["json"] = json
                return _FakeResponse()

        with patch("mesmer.targets.rest.httpx.AsyncClient", _FakeAsyncClient):
            reply = await target.send("ATTACK")

        assert reply == "ok"
        # Body template used the wrapped message. Newlines survive the JSON
        # round-trip in the templated body.
        assert captured["json"] == {"input": "ATTACK\nPOST"}


# ---------------------------------------------------------------------------
# WebSocketTarget — mock `websockets.connect`; capture the payload sent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWebSocketTargetSuffix:
    async def test_send_template_carries_wrapped_message(self):
        target = WebSocketTarget(
            url="ws://unused",
            send_template='{"message": "{{message}}"}',
            receive={"response_field": "response"},
            user_turn_suffix="POST",
        )

        sent: list[str] = []

        class _FakeWS:
            async def send(self, payload):
                sent.append(payload)

            async def recv(self):
                return json.dumps({"response": "ok"})

            async def close(self):
                return None

            @property
            def state(self):  # websockets >=13 open check
                from websockets.protocol import State
                return State.OPEN

        async def _fake_connect(*a, **kw):
            return _FakeWS()

        with patch("mesmer.targets.websocket_target.websockets.connect", _fake_connect):
            reply = await target.send("ATTACK")

        assert reply == "ok"
        assert len(sent) == 1
        payload = json.loads(sent[0])
        # The wrapped message survives _json_escape into the JSON payload.
        assert payload == {"message": "ATTACK\nPOST"}


# ---------------------------------------------------------------------------
# Factory — make sure create_target threads the suffix through for every kind.
# ---------------------------------------------------------------------------


class TestCreateTargetThreadsSuffix:
    def test_openai_adapter(self):
        cfg = SimpleNamespace(
            adapter="openai",
            base_url="http://x",
            url="",
            model="m",
            api_key="k",
            api_key_env="",
            system_prompt="sys",
            user_turn_suffix="POST",
        )
        t = create_target(cfg)
        assert isinstance(t, OpenAITarget)
        assert t.user_turn_suffix == "POST"

    def test_echo_adapter(self):
        cfg = SimpleNamespace(adapter="echo", user_turn_suffix="POST")
        t = create_target(cfg)
        assert isinstance(t, EchoTarget)
        assert t.user_turn_suffix == "POST"

    def test_rest_adapter(self):
        cfg = SimpleNamespace(
            adapter="rest",
            url="http://x",
            method="POST",
            headers={},
            body_template='{"m":"{{message}}"}',
            response_path="m",
            user_turn_suffix="POST",
        )
        t = create_target(cfg)
        assert isinstance(t, RESTTarget)
        assert t.user_turn_suffix == "POST"

    def test_websocket_adapter(self):
        cfg = SimpleNamespace(
            adapter="ws",
            url="ws://x",
            headers={},
            user_turn_suffix="POST",
        )
        t = create_target(cfg)
        assert isinstance(t, WebSocketTarget)
        assert t.user_turn_suffix == "POST"
