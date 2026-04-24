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
            system_fingerprint="fp_abc123",
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


@pytest.mark.asyncio
class TestOpenAITargetFingerprint:
    """Provider-side checkpoint capture — load-bearing for reproducibility
    when model strings (e.g. ``llama-3.1-8b-instant``) don't carry dates."""

    async def test_captures_system_fingerprint_from_response(self):
        target = OpenAITarget(base_url="http://unused", model="m", api_key="k")
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
            system_fingerprint="fp_deadbeef",
        )
        target._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
            return_value=fake_response
        )

        await target.send("hi")
        assert target.last_fingerprint == "fp_deadbeef"

    async def test_leaves_none_when_provider_omits_fingerprint(self):
        """Providers that omit ``system_fingerprint`` leave the field None."""
        target = OpenAITarget(base_url="http://unused", model="m", api_key="k")
        # SimpleNamespace without the attr → getattr returns None → stored as None.
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )
        target._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
            return_value=fake_response
        )

        await target.send("hi")
        assert target.last_fingerprint is None


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
# Target-side throttle — every ``openai_compat`` call goes through a pool
# when a ThrottleConfig is attached. Mirrors the attacker-side retry.py
# contract so provider-side rate limits surface as ThrottleTimeout instead
# of silent send() failures.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenAITargetThrottle:
    async def test_no_pool_when_throttle_is_none(self):
        """Legacy path — no throttle config, no pool, send() unchanged."""
        target = OpenAITarget(base_url="http://unused", model="m", api_key="k")
        assert target._pool is None

    async def test_pool_created_when_throttle_configured(self):
        """Throttle config → pool built via get_or_create_pool → cache-shared."""
        from mesmer.core.keys import ThrottleConfig, clear_pool_cache

        clear_pool_cache()
        throttle = ThrottleConfig(max_rpm=5, max_concurrent=1)
        target = OpenAITarget(
            base_url="http://unused", model="m", api_key="secret-k",
            throttle=throttle,
        )
        assert target._pool is not None
        assert target._pool.throttle.max_rpm == 5
        assert target._pool.throttle.max_concurrent == 1

    async def test_send_acquires_and_releases_on_success(self):
        """Every send() pairs one acquire with one release — no slot leaks."""
        from mesmer.core.keys import ThrottleConfig, clear_pool_cache

        clear_pool_cache()
        target = OpenAITarget(
            base_url="http://unused", model="m", api_key="k",
            throttle=ThrottleConfig(max_concurrent=1),
        )

        # Spy on the pool's acquire / release.
        acquire_spy = AsyncMock()
        release_spy = pytest.MonkeyPatch()
        calls: list[str] = []

        async def fake_acquire(log=None):
            calls.append("acquire")

        def fake_release():
            calls.append("release")

        target._pool.acquire = fake_acquire  # type: ignore[method-assign]
        target._pool.release = fake_release  # type: ignore[method-assign]

        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=None,
        )
        target._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
            return_value=fake_response
        )

        await target.send("hi")
        assert calls == ["acquire", "release"]
        _ = acquire_spy, release_spy  # silence lints

    async def test_send_releases_on_provider_error(self):
        """If the provider call raises, release still fires — no slot leak."""
        from mesmer.core.keys import ThrottleConfig, clear_pool_cache

        clear_pool_cache()
        target = OpenAITarget(
            base_url="http://unused", model="m", api_key="k",
            throttle=ThrottleConfig(max_concurrent=1),
        )

        calls: list[str] = []

        async def fake_acquire(log=None):
            calls.append("acquire")

        def fake_release():
            calls.append("release")

        target._pool.acquire = fake_acquire  # type: ignore[method-assign]
        target._pool.release = fake_release  # type: ignore[method-assign]
        target._client.chat.completions.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("boom"),
        )

        with pytest.raises(RuntimeError, match="boom"):
            await target.send("hi")
        assert calls == ["acquire", "release"]

    async def test_shared_pool_across_targets_with_same_key(self):
        """Two OpenAITargets with the same key share one pool — essential
        for bench trials against the same provider quota (e.g. Groq's
        per-key rate limit)."""
        from mesmer.core.keys import ThrottleConfig, clear_pool_cache

        clear_pool_cache()
        throttle = ThrottleConfig(max_rpm=10, max_concurrent=2)
        t1 = OpenAITarget(
            base_url="http://u", model="m1", api_key="shared-k",
            throttle=throttle,
        )
        t2 = OpenAITarget(
            base_url="http://u", model="m2", api_key="shared-k",
            throttle=throttle,
        )
        assert t1._pool is t2._pool


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
            throttle=None,
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
