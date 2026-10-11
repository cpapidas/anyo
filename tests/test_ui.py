"""Protected browser operations through the installed application's public APIs."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.execution import OperationsSettings
from anyo.execution._operations import WorkerInfo
from anyo.integrations.github import GitHubProvider
from anyo.integrations.omo import omo_harness
from anyo.integrations.sqlite import SqliteQueue
from anyo.observability import LogEntry

SECRET = "ui-webhook-private-secret"
PASSWORD = "ui-operator-private-password"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYO_UI_USERNAME", "operator")
    monkeypatch.setenv("ANYO_UI_PASSWORD", PASSWORD)
    instance = Application(
        providers=[GitHubProvider()],
        harnesses={"omo": omo_harness},
        queue_backends={"sqlite": SqliteQueue},
        queue_url=f"sqlite:///{tmp_path / 'queue.sqlite3'}",
    )
    instance.add_hook(
        provider="github",
        action="pr_created",
        organization="org",
        repository="repo",
        hook_id="review",
        pipeline=lambda event: None,
        secret=SECRET,
    )
    return instance


def delivery(client, identity="one"):
    body = json.dumps(
        {
            "action": "opened",
            "number": 1,
            "repository": {"name": "repo", "owner": {"login": "org"}},
            "pull_request": {"head": {"sha": "a" * 40}},
        }
    ).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-github-event": "pull_request",
            "x-github-delivery": identity,
            "x-hub-signature-256": "sha256="
            + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        },
    )


def login(client):
    response = client.post("/ui/login", json={"username": "operator", "password": PASSWORD})
    assert response.status_code == 200
    session = client.get("/ui/api/session")
    assert session.status_code == 200
    return {"x-anyo-csrf": session.json()["csrf"]}


def test_ui_is_opt_in_and_credentials_fail_closed(app, monkeypatch):
    with TestClient(app.asgi(role="receiver")) as client:
        assert client.get("/ui/").status_code == 404
        assert client.get("/ui/api/runs").status_code == 404
    for name in ("ANYO_UI_USERNAME", "ANYO_UI_PASSWORD"):
        with monkeypatch.context() as patch:
            patch.delenv(name)
            with pytest.raises(ValueError, match="ANYO_UI_USERNAME and ANYO_UI_PASSWORD"):
                app.asgi(ui=True)
    with pytest.raises(ValueError, match="SQLite or Redis"):
        app.asgi(ui=True, queue_url="memory://")


def test_authentication_assets_logout_and_webhook_independence(app):
    with TestClient(app.asgi(ui=True, role="receiver")) as client:
        assert "login-form" in client.get("/ui/").text
        for path in ("runs", "workers", "logs", "session"):
            assert client.get(f"/ui/api/{path}").status_code == 401
        assert client.get("/ui/assets/ui.js").status_code == 200
        assert client.get("/ui/assets/private.py").status_code == 404
        invalid = client.post("/ui/login", json={"username": "operator", "password": "wrong"})
        assert invalid.status_code == 401 and PASSWORD not in invalid.text
        headers = login(client)
        assert (
            "HttpOnly"
            in client.cookies.jar._cookies["testserver.local"]["/ui"]["anyo_session"]._rest
        )
        response = client.get("/ui/")
        assert "runs-view" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert delivery(client).status_code == 202
        assert client.post("/webhooks/github", content=b"{}").status_code == 401
        assert len(client.get("/ui/api/runs").json()["runs"]) == 1
        assert client.post("/ui/logout").status_code == 403
        assert client.post("/ui/logout", headers=headers).status_code == 200
        assert client.get("/ui/api/runs").status_code == 401


def test_csrf_cancel_retry_and_redelivery_policy(app):
    with TestClient(app.asgi(ui=True, role="receiver")) as client:
        headers = login(client)
        delivery(client)
        run = client.get("/ui/api/runs").json()["runs"][0]
        url = f"/ui/api/runs/{run['id']}/cancel"
        assert client.post(url).status_code == 403
        assert (
            client.post(url, headers={**headers, "origin": "https://other.example"}).status_code
            == 403
        )
        assert client.get(url).status_code == 405
        assert (
            client.post(url, headers=headers, json={"expected_token": "stale"}).status_code == 409
        )
        assert (
            client.post(
                url, headers=headers, json={"expected_token": run.get("attempt_token") or ""}
            ).status_code
            == 202
        )
        assert client.get("/ui/api/runs").json()["runs"][0]["status"] == "cancelled"
        assert delivery(client).json() == {"accepted": 0}
        assert client.post(f"/ui/api/runs/{run['id']}/retry", headers=headers).status_code == 202
        assert (
            client.post(
                url, headers=headers, json={"expected_token": run["attempt_token"]}
            ).status_code
            == 409
        )
        assert client.get("/ui/api/runs?status=queued").json()["runs"][0]["status"] == "queued"
        assert client.get("/ui/api/runs?status=successful").json()["runs"] == []


def test_login_limits_origin_and_secure_cookie(app):
    with TestClient(app.asgi(ui=True, role="receiver"), base_url="https://testserver") as client:
        assert (
            client.post("/ui/login", json={}, headers={"origin": "https://bad.example"}).status_code
            == 403
        )
        response = client.post("/ui/login", json={"username": "operator", "password": PASSWORD})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        for _ in range(10):
            assert (
                client.post("/ui/login", json={"username": "x", "password": "bad"}).status_code
                == 401
            )
        assert (
            client.post(
                "/ui/login", json={"username": "operator", "password": PASSWORD}
            ).status_code
            == 429
        )


def test_invalid_and_oversized_login_request(app):
    with TestClient(app.asgi(ui=True, role="receiver")) as client:
        assert client.post("/ui/login", content="username=operator").status_code == 400
        assert client.post("/ui/login", json={"password": "x" * 5000}).status_code == 400
        assert client.post("/ui/login", json=[]).status_code == 400
        assert client.post("/ui/login", json={"username": {}, "password": []}).status_code == 401


def test_worker_controls_reject_unknown_stale_or_unsafe_targets(app, monkeypatch):
    with TestClient(app.asgi(ui=True, role="receiver")) as client:
        headers = login(client)
        backend = client.app.state.dispatcher.operations_backend
        client.portal.call(backend.worker_heartbeat, WorkerInfo("thread", heartbeat_at=time.time()))
        client.portal.call(
            backend.worker_heartbeat, WorkerInfo("stale", execution_mode="process", heartbeat_at=1)
        )
        read_workers = backend.workers

        async def with_stale_worker():
            return [
                replace(worker, heartbeat_at=1) if worker.id == "stale" else worker
                for worker in await read_workers()
            ]

        monkeypatch.setattr(backend, "workers", with_stale_worker)
        assert client.post("/ui/api/workers/thread/force_stop", headers=headers).status_code == 409
        assert client.post("/ui/api/workers/stale/force_stop", headers=headers).status_code == 409
        assert (
            client.post("/ui/api/workers/no-such-worker/stop", headers=headers).status_code == 404
        )
        assert client.post("/ui/api/workers/thread/stop", headers=headers).status_code == 202
        workers = client.get("/ui/api/workers").json()["workers"]
        assert next(w for w in workers if w["id"] == "thread")["command"] == "drain"


def test_log_history_filters_and_namespace(app):
    with TestClient(app.asgi(ui=True, role="receiver", queue_namespace="test-ui")) as client:
        login(client)
        backend = client.app.state.dispatcher.operations_backend
        client.portal.call(
            backend.append_logs,
            [
                LogEntry(
                    "<script>alert(1)</script>", source="pipeline", run_id="run-one", level="ERROR"
                ),
                LogEntry("general entry", source="receiver"),
            ],
        )
        page = client.get("/ui/api/logs?run_id=run-one&level=ERROR").json()
        assert len(page["entries"]) == 1
        assert page["entries"][0]["message"] == "<script>alert(1)</script>"
        assert (
            client.get(f"/ui/api/logs?after={page['next_cursor']}&run_id=run-one").json()["entries"]
            == []
        )
        assert client.get("/ui/api/logs?after=-1").status_code == 400
        assert client.get("/ui/api/logs?after=no").status_code == 400
        assert client.get("/ui/api/session").json()["namespace"] == "test-ui"
    with TestClient(app.asgi(ui=True, role="receiver", queue_namespace="test-ui")) as client:
        login(client)
        assert (
            client.get("/ui/api/logs?source=pipeline").json()["entries"][0]["run_id"] == "run-one"
        )


def test_session_expiry(app, monkeypatch):
    monkeypatch.setattr("anyo.ui._server._SESSION_SECONDS", 0)
    with TestClient(app.asgi(ui=True, role="receiver")) as client:
        assert (
            client.post(
                "/ui/login", json={"username": "operator", "password": PASSWORD}
            ).status_code
            == 200
        )
        assert client.get("/ui/api/session").status_code == 401


def test_general_logs_redacted_before_ui_history(app):
    model_key = "configured-model-key-before-first-prompt"
    app.harness(provider="omo", models=[{"model": "minimax/test", "key": model_key}])
    with TestClient(app.asgi(ui=True, role="receiver", operations=OperationsSettings())) as client:
        login(client)

        async def emit():
            logging.getLogger("anyo.application").warning(
                "credentials %s %s %s", PASSWORD, SECRET, model_key
            )
            async with asyncio.timeout(5):
                while True:
                    page = await client.app.state.dispatcher.operations_backend.logs()
                    if any("credentials" in entry.message for entry in page.entries):
                        return
                    await asyncio.sleep(0.01)

        client.portal.call(emit)
        logs = client.get("/ui/api/logs").json()["entries"]
        captured = next(entry for entry in logs if "credentials" in entry["message"])
        assert PASSWORD not in captured["message"] and SECRET not in captured["message"]
        assert model_key not in captured["message"]
        assert "[redacted]" in captured["message"]
