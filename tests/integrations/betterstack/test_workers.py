"""Separate interpreter and spawned pipeline verification with both queue backends."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.integrations.betterstack import BetterStackProvider
from anyo.integrations.redis import RedisQueue
from anyo.integrations.sqlite import SqliteQueue

from .conftest import add, error, incident, post


def exercise(url, mode, tmp_path):
    namespace = "betterstack-tests-" + uuid4().hex
    app = Application(
        providers=[BetterStackProvider()],
        queue_backends={"sqlite": SqliteQueue, "redis": RedisQueue},
        queue_url=url,
    )
    for family in ("incident", "error"):
        add(app, lambda event: None, action=family, hook_id=family)
    try:
        with TestClient(app.asgi(role="receiver", queue_namespace=namespace)) as client:
            for payload in (incident(None), error("reoccurred")):
                payload["output_directory"] = str(tmp_path)
                assert post(client, payload).status_code == 200
            assert {r.status for r in client.portal.call(client.app.state.dispatcher.inspect)} == {
                "queued"
            }
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("ANYO_BETTERSTACK_WEBHOOK_SECRET", None)
        script = Path(__file__).with_name("worker_consumer.py")
        for _ in range(2):  # Replacement worker sees success and does not repeat jobs.
            result = subprocess.run(
                [sys.executable, "-I", str(script), url, namespace, mode],
                cwd=tmp_path,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        incident_result = json.loads((tmp_path / "incident.json").read_text())
        error_result = json.loads((tmp_path / "error.json").read_text())
        assert incident_result["event_type"] == "unspecified"
        assert incident_result["monitor_id"] is None
        assert error_result["event_type"] == "reoccurred"
        assert error_result["team_id"] == "12345"
        assert error_result["application_id"] == "4821"
        assert (
            error_result["payload"]["data"]["attributes"]["error_message"]
            == "Private application message"
        )
        with TestClient(app.asgi(role="receiver", queue_namespace=namespace)) as client:
            payload = error("reoccurred")
            payload["output_directory"] = str(tmp_path)
            assert post(client, payload).status_code == 200
            assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 2
    finally:
        if url.startswith("redis://"):
            import redis

            prefix = "anyo:queue:{" + hashlib.sha256(namespace.encode()).hexdigest() + "}"
            with redis.Redis.from_url(url) as client:
                keys = list(client.scan_iter(match=prefix + ":*"))
                if keys:
                    client.delete(*keys)


@pytest.mark.parametrize("mode", ["thread", "process"])
def test_sqlite_separate_worker_preserves_context(tmp_path, mode):
    if mode == "process" and os.name != "posix":
        pytest.skip("Process execution requires POSIX")
    exercise(f"sqlite:///{tmp_path / 'queue.db'}", mode, tmp_path)


@pytest.mark.live
@pytest.mark.parametrize("mode", ["thread", "process"])
def test_redis_separate_worker_preserves_context(tmp_path, mode):
    if mode == "process" and os.name != "posix":
        pytest.skip("Process execution requires POSIX")
    url = os.environ.get("ANYO_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set ANYO_TEST_REDIS_URL to an isolated test Redis")
    pytest.importorskip("redis")
    exercise(url, mode, tmp_path)
