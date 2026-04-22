"""Target adapters — connect to any LLM endpoint."""

from mesmer.targets.base import Target, Turn
from mesmer.targets.openai_compat import OpenAITarget
from mesmer.targets.rest import RESTTarget
from mesmer.targets.echo import EchoTarget
from mesmer.targets.websocket_target import WebSocketTarget

__all__ = ["Target", "Turn", "OpenAITarget", "RESTTarget", "EchoTarget", "WebSocketTarget"]


def create_target(config) -> Target:
    """Factory: create a target from a TargetConfig."""
    adapter = config.adapter.lower()

    if adapter == "openai":
        return OpenAITarget(
            base_url=config.base_url or config.url or "https://api.openai.com/v1",
            model=config.model,
            api_key=config.api_key,
            api_key_env=config.api_key_env,
            system_prompt=config.system_prompt,
        )
    elif adapter == "rest":
        return RESTTarget(
            url=config.url,
            method=config.method,
            headers=config.headers,
            body_template=config.body_template,
            response_path=config.response_path,
        )
    elif adapter in ("websocket", "ws"):
        return WebSocketTarget(
            url=config.url,
            send_template=getattr(config, "send_template", '{"message": "{{message}}"}'),
            receive=getattr(config, "receive", None),
            connect_signal=getattr(config, "connect_signal", None),
            query_params=getattr(config, "query_params", {}),
            headers=config.headers,
            connect_timeout=getattr(config, "connect_timeout", 10.0),
            receive_timeout=getattr(config, "receive_timeout", 90.0),
        )
    elif adapter == "echo":
        return EchoTarget()
    else:
        raise ValueError(f"Unknown target adapter: {adapter}")
