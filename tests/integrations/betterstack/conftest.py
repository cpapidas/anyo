"""Documentation-derived envelopes, not captured Better Stack deliveries."""

import json

import pytest

from anyo.application import Application
from anyo.integrations.betterstack import BetterStackProvider
from anyo.integrations.sqlite import SqliteQueue

SECRET = "local-betterstack-test-secret"
HEADERS = {"content-type": "application/json", "x-anyo-webhook-secret": SECRET}


def incident(event="started", **attributes):
    result = {
        "data": {
            "id": "12",
            "type": "incident",
            "attributes": {
                "name": "Example API",
                "cause": "Error 500",
                "started_at": "2026-10-11T12:00:00Z",
                "acknowledged_at": None,
                "resolved_at": None,
                **attributes,
            },
        },
    }
    if event is not None:
        result["event"] = event
    if event == "comment":
        result["comment"] = {
            "id": "19",
            "type": "incident_comment",
            "attributes": {"content": "Investigating", "created_at": "2026-10-11T12:05:00Z"},
        }
    return result


def error(event="new_error", **attributes):
    return {
        "data": {
            "id": "abc123",
            "type": "error",
            "attributes": {
                "event": event,
                "application_id": 4821,
                "team_id": 12345,
                "environment": "production",
                "error_url": "https://errors.betterstack.com/team/12345/errors/abc123?s=4821",
                "error_message": "Private application message",
                "first_seen": "2026-10-11T12:00:00Z",
                "last_seen": "2026-10-11T12:00:00Z",
                "notify_threshold": None,
                "previous_state": None,
                **attributes,
            },
        }
    }


@pytest.fixture
def provider():
    return BetterStackProvider()


@pytest.fixture
def app(tmp_path):
    return Application(
        providers=[BetterStackProvider()],
        queue_backends={"sqlite": SqliteQueue},
        queue_url=f"sqlite:///{tmp_path / 'queue.sqlite3'}",
    )


def add(app, pipeline, *, action="incident", event_type="*", hook_id="incident", **kwargs):
    return app.add_hook(
        provider="betterstack",
        action=action,
        event_type=event_type,
        pipeline=pipeline,
        hook_id=hook_id,
        **{"secret": SECRET, **kwargs},
    )


def post(client, payload=None, *, headers=None):
    return client.post(
        "/webhooks/betterstack",
        content=json.dumps(incident() if payload is None else payload),
        headers=HEADERS if headers is None else headers,
    )
