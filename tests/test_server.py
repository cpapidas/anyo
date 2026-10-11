"""Installed-consumer startup and real local HTTP server lifecycle."""

from __future__ import annotations

import ast
import hashlib
import hmac
import http.client
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from anyo.application import Application
from anyo.integrations.github import GitHubProvider

SECRET = "test-only-subprocess-secret"
TIMEOUT = 15


class _ProcessOutput:
    """Collect output continuously; wait on messages instead of polling sleeps."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        self.lines: list[str] = []
        self.pending: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read, args=(process.stdout,), daemon=True)
        self.reader.start()

    def _read(self, stream) -> None:
        try:
            for line in stream:
                self.pending.put(line.rstrip())
        finally:
            self.pending.put(None)

    def wait_for(self, predicate: Callable[[str], bool]) -> str:
        for line in self.lines:
            if predicate(line):
                return line
        deadline = time.monotonic() + TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            try:
                line = self.pending.get(timeout=max(0, remaining))
            except queue.Empty:
                pytest.fail(f"Timed out waiting for child output:\n{self.transcript()}")
            if line is None:
                pytest.fail(f"Child exited before expected output:\n{self.transcript()}")
            self.lines.append(line)
            if predicate(line):
                return line

    def transcript(self) -> str:
        return "\n".join(self.lines)


@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX graceful SIGTERM shutdown")
def test_installed_main_serves_webhooks_and_drains_on_sigterm(tmp_path: Path) -> None:
    # This file runs outside the checkout and imports the installed distribution.
    # Standard input is a control channel for deterministic callback barriers.
    main = tmp_path / "main.py"
    result = tmp_path / "result.json"
    main.write_text(
        textwrap.dedent(
            """\
            import json
            import logging
            import sys
            import threading
            from pathlib import Path

            import anyo

            logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
            started = threading.Event()
            release = threading.Event()

            def control():
                for line in sys.stdin:
                    if line.strip() == "status":
                        print("STATUS_RUNNING" if started.is_set() else "STATUS_IDLE", flush=True)
                    elif line.strip() == "release":
                        release.set()

            def pipeline(event):
                started.set()
                print("PIPELINE_STARTED", flush=True)
                if not release.wait(30):
                    raise TimeoutError("Test callback was never released")
                Path("result.json").write_text(json.dumps({
                    "delivery": event.delivery_id,
                    "repository": event.repository,
                    "number": event.pull_request_number,
                    "sha": event.head_sha,
                }))
                print("PIPELINE_COMPLETED", flush=True)

            anyo.add_hook(
                provider="github", action="pr_created", organization="owner",
                repository="repository", pipeline=pipeline,
            )
            threading.Thread(target=control, daemon=True).start()
            anyo.start(port=0, workers=1)
            """
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["ANYO_GITHUB_WEBHOOK_SECRET"] = SECRET
    process = subprocess.Popen(
        [sys.executable, "-u", str(main)],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output = _ProcessOutput(process)
    assert process.stdin is not None

    def command(value: str) -> None:
        process.stdin.write(value + "\n")
        process.stdin.flush()

    try:
        address_line = output.wait_for(lambda line: line.startswith("Anyo listening on "))
        address = ast.literal_eval(address_line.removeprefix("Anyo listening on "))
        host, port = address
        assert host == "127.0.0.1" and port > 0
        output.wait_for(lambda line: line == "Application startup complete.")
        command("status")
        output.wait_for(lambda line: line == "STATUS_IDLE")
        assert not result.exists()

        body = json.dumps(
            {
                "action": "opened",
                "number": 17,
                "repository": {"owner": {"login": "owner"}, "name": "repository"},
                "pull_request": {"head": {"sha": "a" * 40}},
            }
        ).encode()
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        try:
            connection.request(
                "POST",
                "/webhooks/github",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Delivery": "subprocess-delivery",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": f"sha256={digest}",
                },
            )
            response = connection.getresponse()
            assert response.status == 202
            assert json.loads(response.read()) == {"accepted": 1}
        finally:
            connection.close()

        output.wait_for(lambda line: line == "PIPELINE_STARTED")
        # The callback cannot finish yet, proving acknowledgment did not wait.
        assert not result.exists()
        process.send_signal(signal.SIGTERM)
        output.wait_for(lambda line: line == "Waiting for application shutdown.")
        assert process.poll() is None
        assert not result.exists()
        command("release")
        output.wait_for(lambda line: line == "PIPELINE_COMPLETED")
        output.wait_for(lambda line: line == "Application shutdown complete.")
        return_code = process.wait(timeout=TIMEOUT)
        # Uvicorn restores the original handler and re-raises SIGTERM only after
        # graceful shutdown has completed; a negative code is expected on POSIX.
        assert return_code == -signal.SIGTERM, output.transcript()
        assert json.loads(result.read_text()) == {
            "delivery": "subprocess-delivery",
            "repository": "repository",
            "number": 17,
            "sha": "a" * 40,
        }
        with socket.create_server((host, port)):
            pass  # The original listening port is released after shutdown.
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=TIMEOUT)
        process.stdin.close()
        output.reader.join(timeout=TIMEOUT)
        assert not output.reader.is_alive()
        assert process.stdout is not None
        process.stdout.close()


def test_server_system_exit_becomes_exception_and_releases_resources(monkeypatch) -> None:
    application = Application(providers=[GitHubProvider()])
    application.add_hook(
        provider="github",
        action="pr_created",
        organization="owner",
        repository="repository",
        pipeline=lambda event: None,
        secret=SECRET,
    )
    sockets = []

    def fail_start(server, *, sockets):
        owned_sockets.extend(sockets)
        raise SystemExit(3)

    owned_sockets = sockets
    monkeypatch.setattr("uvicorn.Server.run", fail_start)
    with pytest.raises(RuntimeError, match="server failed to start"):
        application.start(port=0)
    assert len(sockets) == 1
    assert sockets[0].fileno() == -1
    # Releasing the running claim also lets the consumer register further hooks.
    application.add_hook(
        provider="github",
        action="pr_created",
        organization="owner",
        repository="another",
        pipeline=lambda event: None,
        secret=SECRET,
    )
