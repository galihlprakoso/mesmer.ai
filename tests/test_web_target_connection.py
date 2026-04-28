"""Web target connection probe tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mesmer.interfaces.web.backend.server import create_app


def test_target_connection_probe_echo_target(tmp_path):
    scenario = tmp_path / "echo.yaml"
    scenario.write_text(
        "\n".join(
            [
                "name: Echo target",
                "description: Probe echo connectivity",
                "target:",
                "  adapter: echo",
                "objective:",
                "  goal: Test connectivity",
                "modules: [system-prompt-extraction]",
                "agent:",
                "  model: test/model",
                "  api_key: sk-test",
                "",
            ]
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(scenario_dir=str(tmp_path)))

    res = client.post(
        "/api/target/test",
        json={"scenario_path": str(scenario), "message": "ping"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["adapter"] == "echo"
    assert body["response_preview"] == "Echo: ping"
    assert isinstance(body["latency_ms"], int | float)


def test_target_connection_probe_reports_bad_scenario(tmp_path):
    client = TestClient(create_app(scenario_dir=str(tmp_path)))

    res = client.post(
        "/api/target/test",
        json={"scenario_path": str(tmp_path / "missing.yaml")},
    )

    assert res.status_code == 400
    body = res.json()
    assert body["ok"] is False
    assert "Target config failed" in body["error"]
