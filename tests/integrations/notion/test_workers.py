"""Real SQLite/Redis transport, independent workers, and process execution."""

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
from anyo.integrations.notion import NotionProvider
from anyo.integrations.redis import RedisQueue
from anyo.integrations.sqlite import SqliteQueue

from .conftest import ENTITY, add, payload, post


def exercise(url, mode, tmp_path):
    namespace = "notion-tests-" + uuid4().hex
    app = Application(
        providers=[NotionProvider()],
        queue_backends={"sqlite": SqliteQueue, "redis": RedisQueue},
        queue_url=url,
    )
    add(app, lambda event: None)
    natives = ("page.deleted", "file_upload.completed", "data_source.schema_updated")
    try:
        with TestClient(app.asgi(role="receiver", queue_namespace=namespace)) as client:
            for native in natives:
                data = payload(native, output_directory=str(tmp_path))
                assert post(client, data).status_code == 200
            assert {r.status for r in client.portal.call(client.app.state.dispatcher.inspect)} == {
                "queued"
            }
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("ANYO_NOTION_VERIFICATION_TOKEN", None)
        script = Path(__file__).with_name("worker_consumer.py")
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-I", str(script), url, namespace, mode],
                cwd=tmp_path,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        for native in natives:
            data = json.loads((tmp_path / f"{native}.json").read_text())
            assert data["event_type"] == native and data["entity_id"] == ENTITY
            assert data["payload"]["workspace_name"] == "Private workspace name"
            assert data["attempt_number"] == 1 and data["api_version"] == "2026-03-11"
            if native.startswith("file_upload"):
                assert "data" not in data["payload"]
            if native.startswith("data_source"):
                assert data["entity_type"] is None
        with TestClient(app.asgi(role="receiver", queue_namespace=namespace)) as client:
            for native in natives:
                data = payload(native, output_directory=str(tmp_path), attempt_number=8)
                assert post(client, data).status_code == 200
            assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 3
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
