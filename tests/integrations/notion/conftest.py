"""Sanitized documentation-derived envelopes; these are not live captures.

Sources and legacy/new-schema differences: docs/tasks/notion-hooks/research.md.
The common envelope is shared; family-specific data stays deliberately minimal.
"""

import hashlib
import hmac
import json

import pytest

from anyo.application import Application
from anyo.integrations.notion import NotionProvider
from anyo.integrations.sqlite import SqliteQueue

SECRET = "local-notion-verification-token"
WORKSPACE = "11111111-1111-4111-8111-111111111111"
SUBSCRIPTION = "22222222-2222-4222-8222-222222222222"
INTEGRATION = "33333333-3333-4333-8333-333333333333"
ENTITY = "44444444-4444-4444-8444-444444444444"
NATIVE_EVENTS = (
    "page.created",
    "page.content_updated",
    "page.properties_updated",
    "page.moved",
    "page.deleted",
    "page.undeleted",
    "page.locked",
    "page.unlocked",
    "page.transcription_block.transcript_deleted",
    "database.created",
    "database.moved",
    "database.deleted",
    "database.undeleted",
    "database.content_updated",
    "database.schema_updated",
    "data_source.created",
    "data_source.content_updated",
    "data_source.schema_updated",
    "data_source.moved",
    "data_source.deleted",
    "data_source.undeleted",
    "comment.created",
    "comment.updated",
    "comment.deleted",
    "file_upload.created",
    "file_upload.completed",
    "file_upload.expired",
    "file_upload.upload_failed",
    "view.created",
    "view.updated",
    "view.deleted",
)


def payload(native="page.content_updated", **overrides):
    family = native.split(".")[0]
    result = {
        "id": "event-" + native,
        "type": native,
        "workspace_id": WORKSPACE,
        "workspace_name": "Private workspace name",
        "integration_id": INTEGRATION,
        "subscription_id": SUBSCRIPTION,
        "entity": {"id": ENTITY, "type": family},
        "timestamp": "2026-10-12T10:00:00.123Z",
        "attempt_number": 1,
        "api_version": "2026-03-11",
        "authors": [{"id": INTEGRATION, "type": "bot"}],
    }
    if family != "file_upload":
        result["data"] = {"parent": {"id": ENTITY}}
    if family == "comment":
        result["data"].update(page_id=ENTITY, discussion_id=WORKSPACE)
    if family in {"database", "data_source"}:
        del result["entity"]["type"]  # Current generated examples omit this field.
    if native in {"database.content_updated", "database.schema_updated"}:
        result["api_version"] = "2022-06-28"
        result["entity"]["type"] = "database"
    result.update(overrides)
    return result


def headers(body, secret=SECRET):
    return {
        "content-type": "application/json",
        "x-notion-signature": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
    }


def post(client, data=None, *, secret=SECRET):
    body = json.dumps(payload() if data is None else data, ensure_ascii=False).encode()
    return client.post("/webhooks/notion", content=body, headers=headers(body, secret))


def add(app, pipeline, *, event_type="*", hook_id="notion", **kwargs):
    return app.add_hook(
        provider="notion",
        action="event",
        event_type=event_type,
        pipeline=pipeline,
        hook_id=hook_id,
        **{"secret": SECRET, **kwargs},
    )


@pytest.fixture
def app(tmp_path):
    return Application(
        providers=[NotionProvider()],
        queue_backends={"sqlite": SqliteQueue},
        queue_url=f"sqlite:///{tmp_path / 'queue.sqlite3'}",
    )
