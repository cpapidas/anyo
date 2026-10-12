"""The candidate collector cannot authenticate, dispatch, or replace a secret."""

import asyncio
import http.client
import json
import os
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
from contextlib import closing

import pytest
from starlette.testclient import TestClient

from anyo.integrations.notion import _setup

from .conftest import SECRET, payload

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Setup permissions require POSIX")


@pytest.fixture
def target(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return directory / "candidate"


def test_candidate_permissions_replay_conflict_and_event_rejection(target, caplog, capsys):
    app = _setup.application(target)
    with TestClient(app) as client:
        assert client.post("/webhooks/notion", json=payload()).status_code == 503
        assert not target.exists()
        for _ in range(2):
            response = client.post("/webhooks/notion", json={"verification_token": SECRET})
            assert response.status_code == 200 and not response.content
        assert target.read_text() == SECRET
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert (
            client.post("/webhooks/notion", json={"verification_token": "other"}).status_code == 409
        )
        assert client.post("/webhooks/notion", json=payload()).status_code == 503
        assert client.get("/webhooks/notion").status_code == 405
        assert client.post("/other", json={}).status_code == 404
    assert list(target.parent.iterdir()) == [target]
    assert SECRET not in caplog.text + capsys.readouterr().out


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"verification_token": ""},
        {"verification_token": None},
        {"verification_token": "a\nb"},
        {"verification_token": "é"},
        {"verification_token": "x" * 4097},
        {"verification_token": SECRET, "id": "event"},
    ],
)
def test_rejects_invalid_candidate_envelopes(target, data):
    with TestClient(_setup.application(target)) as client:
        result = client.post("/webhooks/notion", json=data)
        assert result.status_code == (503 if data == {} else 400)
        assert SECRET not in result.text
    assert not target.exists()


def test_bad_json_media_and_body_bounds(target):
    with TestClient(_setup.application(target)) as client:
        for body in (
            b"null",
            b"[]",
            b'{"verification_token":"a","verification_token":"b"}',
            b'{"verification_token":NaN}',
            b'{"verification_token":"\\ud800"}',
        ):
            assert (
                client.post(
                    "/webhooks/notion", content=body, headers={"content-type": "application/json"}
                ).status_code
                == 400
            )
        assert client.post("/webhooks/notion", content=b"{}").status_code == 415
        assert (
            client.post(
                "/webhooks/notion",
                content=b"x" * 16385,
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/webhooks/notion",
                content=b"{}",
                headers={"content-type": "application/json", "content-length": "-1"},
            ).status_code
            == 400
        )
    assert not target.exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "insecure-directory"])
def test_existing_destinations_and_insecure_permissions_refused(target, kind):
    other = target.parent / "other"
    if kind == "file":
        target.write_text("original")
    elif kind == "symlink":
        other.write_text("original")
        target.symlink_to(other)
    else:
        target.parent.chmod(0o755)
    with pytest.raises(ValueError):
        with TestClient(_setup.application(target)):
            pass
    if kind != "insecure-directory":
        assert target.read_text() == "original"


def test_concurrent_requests_store_only_one_candidate(target):
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(_setup.application(target)) as client:

        def send(token):
            return client.post("/webhooks/notion", json={"verification_token": token}).status_code

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(send, ["first-candidate", "second-candidate"]))
    assert sorted(results) == [200, 409]
    assert target.read_text() in {"first-candidate", "second-candidate"}


def test_write_failure_removes_partial_file_and_allows_retry(target, monkeypatch, caplog):
    original = os.fsync
    with TestClient(_setup.application(target)) as client:

        def fail(fd):
            raise OSError("private failure " + SECRET)

        monkeypatch.setattr(os, "fsync", fail)
        response = client.post("/webhooks/notion", json={"verification_token": SECRET})
        assert response.status_code == 503
        assert not target.exists() and SECRET not in response.text
        monkeypatch.setattr(os, "fsync", original)
        assert (
            client.post("/webhooks/notion", json={"verification_token": SECRET}).status_code == 200
        )
    assert target.read_text() == SECRET and SECRET not in caplog.text


def test_file_created_after_startup_is_not_overwritten(target):
    with TestClient(_setup.application(target)) as client:
        target.write_text("created elsewhere")
        assert (
            client.post("/webhooks/notion", json={"verification_token": SECRET}).status_code == 409
        )
    assert target.read_text() == "created elsewhere"


def test_saved_candidate_survives_acknowledgment_failure(target):
    async def run():
        app = _setup.application(target)
        body = json.dumps({"verification_token": SECRET}).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def fail_send(message):
            raise ConnectionError("client disconnected")

        async with app.router.lifespan_context(app):
            with pytest.raises(ConnectionError):
                await app(scope(), receive, fail_send)
            assert target.read_text() == SECRET

    asyncio.run(run())


def scope():
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/webhooks/notion",
        "raw_path": b"/webhooks/notion",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("localhost", 8000),
        "client": ("localhost", 12345),
    }


@pytest.mark.parametrize(
    "mode,expected", [("timeout", 408), ("disconnect", 400), ("oversize", 413)]
)
def test_stream_limits_without_content_length(target, mode, expected):
    async def run():
        app = _setup.application(target, read_timeout=0.01)
        messages = []

        async def receive():
            if mode == "timeout":
                await asyncio.Event().wait()
            if mode == "disconnect":
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"x" * 16385, "more_body": True}

        async def send(message):
            messages.append(message)

        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(scope(), receive, send), 2)
        assert messages[0]["status"] == expected

    asyncio.run(run())
    assert not target.exists()


def test_cancelled_read_leaves_no_output_and_closes_resources(target):
    async def run():
        entered = asyncio.Event()
        app = _setup.application(target)

        async def receive():
            entered.set()
            await asyncio.Event().wait()

        async def send(message):
            raise AssertionError("No response expected")

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app(scope(), receive, send))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # A fresh lifecycle can open the directory; no candidate was saved.
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run())
    assert not target.exists()


def command(target, *options):
    return [
        sys.executable,
        "-I",
        "-m",
        "anyo.integrations.notion",
        "verify",
        "--token-file",
        str(target),
        *options,
    ]


def test_cli_real_listener_keeps_events_retryable_and_stops(target, tmp_path):
    process = subprocess.Popen(
        command(target, "--port", "0", "--timeout", "10"),
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = queue.Queue()

    def read_output():
        for line in process.stdout:
            lines.put(line)

    reader = threading.Thread(target=read_output)
    reader.start()
    captured = []
    try:
        ready = lines.get(timeout=5)
        captured.append(ready)
        port = int(re.search(r"127\.0\.0\.1:(\d+)/", ready)[1])
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as client:
            for data, status in (({"verification_token": SECRET}, 200), (payload(), 503)):
                client.request(
                    "POST",
                    "/webhooks/notion",
                    json.dumps(data),
                    {"Content-Type": "application/json"},
                )
                response = client.getresponse()
                assert response.status == status
                assert SECRET.encode() not in response.read()
        captured.append(lines.get(timeout=5))
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0
        reader.join(5)
        assert not reader.is_alive()
        assert SECRET not in "".join(captured) + process.stderr.read()
        assert target.read_text() == SECRET
        with socket.create_server(("127.0.0.1", port)):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(5)
        process.stdout.close()
        process.stderr.close()


def test_cli_timeout_and_bind_failure_close_listener(target, tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        failed = subprocess.run(
            command(target, "--port", str(port)),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert failed.returncode == 1 and not target.exists()
    result = subprocess.run(
        command(target, "--port", str(port), "--timeout", "0.1"),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1 and not target.exists()
    assert "without collecting" in result.stdout
    with socket.create_server(("127.0.0.1", port)):
        pass
