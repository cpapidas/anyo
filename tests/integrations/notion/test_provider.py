"""Public provider, registration, native envelope, and event codec contracts."""

import json
from dataclasses import asdict, replace

import pytest

from anyo.hooks import HookEvent, Provider, ResponderCapability, ResponseProvider, WebhookError
from anyo.integrations.notion import NotionEvent, NotionProvider

from .conftest import ENTITY, NATIVE_EVENTS, SECRET, SUBSCRIPTION, WORKSPACE, headers, payload


def decode(data, provider=None):
    body = json.dumps(data, ensure_ascii=False).encode()
    return (provider or NotionProvider()).decode(body, headers(body), SECRET).event


@pytest.mark.parametrize("native", NATIVE_EVENTS)
def test_native_catalog_and_codec_roundtrip(native):
    provider = NotionProvider()
    result = decode(payload(native), provider)
    assert type(result) is NotionEvent
    assert result.event_type == native and result.entity_id == ENTITY
    assert result.provider == "notion" and result.action == "event"
    assert result.organization == result.repository == ""
    assert result.head_sha is result.pull_request_number is None
    assert provider.matches(provider.validate("event", {"event_type": native}), result)
    assert provider.matches(provider.validate("event", {"event_type": "*"}), result)
    assert not provider.matches({"event_type": "other.event"}, result)
    assert provider.event_codec.decode(provider.event_codec.encode(result)) == result


def test_public_protocols_and_private_payload_representation():
    provider: Provider = NotionProvider()
    assert isinstance(provider, ResponseProvider)
    assert isinstance(provider, ResponderCapability) and not provider.supports_responders
    event = decode(payload(private="Private note"))
    assert "Private" not in repr(event)
    assert not provider.matches({}, HookEvent("notion", "event", "1"))


def test_source_scoped_identity_ignores_attempt_time_and_content():
    first = decode(payload())
    again = decode(payload(attempt_number=9, timestamp="2020-01-01T00:00:00Z", extra="changed"))
    assert first.delivery_id == again.delivery_id
    for key, value in (
        ("id", "new-event"),
        ("subscription_id", WORKSPACE),
        ("workspace_id", SUBSCRIPTION),
        ("integration_id", WORKSPACE),
    ):
        assert decode(payload(**{key: value})).delivery_id != first.delivery_id
    assert decode(payload(id="opaque, event:α")).event_id == "opaque, event:α"


def test_context_uuid_normalization_and_missing_entity_type():
    data = payload(workspace_id=WORKSPACE.replace("-", "").upper())
    data["entity"] = {"id": ENTITY}
    event = decode(data)
    provider = NotionProvider()
    config = provider.validate("event", {"event_type": "*", "workspace_id": WORKSPACE})
    assert provider.matches(config, event)
    assert event.workspace_id == WORKSPACE and event.entity_type is None
    assert event.payload["workspace_id"] == data["workspace_id"]
    assert not provider.matches({**config, "entity_type": "page"}, event)
    assert not provider.matches({**config, "subscription_id": WORKSPACE}, event)
    assert not provider.matches({**config, "entity_id": SUBSCRIPTION}, event)


@pytest.mark.parametrize("version", [None, "2022-06-28", "2025-09-03", "2026-03-11", "2099-01-01"])
def test_legacy_current_and_additive_future_context(version):
    data = payload("future.native_event", future={"nested": [None, 1.2, True]})
    if version is None:
        del data["api_version"]
    else:
        data["api_version"] = version
    data["authors"].append({"id": ENTITY, "type": "agent", "new_field": True})
    event = decode(data)
    assert event.api_version == version and event.event_type == "future.native_event"
    assert event.payload == data


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", ""),
        ("id", "x" * 513),
        ("id", "newline\n"),
        ("type", "page"),
        ("type", "page.*"),
        ("workspace_id", "bad"),
        ("integration_id", None),
        ("subscription_id", True),
        ("entity", {}),
        ("entity", {"id": ENTITY, "type": None}),
        ("entity", {"id": "not-an-id"}),
        ("timestamp", "yesterday"),
        ("timestamp", "2026-10-12"),
        ("timestamp", "2026-10-12T10:00:00"),
        ("attempt_number", 0),
        ("attempt_number", True),
        ("attempt_number", 1.2),
        ("attempt_number", None),
        ("api_version", "2026-99-01"),
        ("api_version", None),
        ("authors", None),
        ("authors", [{}]),
        ("accessible_by", False),
        ("workspace_name", 1),
        ("data", []),
        ("verification_token", "candidate"),
    ],
)
def test_invalid_envelopes_are_safe_errors(key, value):
    with pytest.raises(WebhookError, match="Invalid Notion webhook body") as caught:
        decode(payload(**{key: value}))
    assert caught.value.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b'{"id":"a","id":"b"}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":"\\ud800"}',
        b'{"x":{"a":1,"a":2}}',
        b"\xff",
        b"[" * 1000 + b"]" * 1000,
    ],
)
def test_nonportable_json(body):
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(body, headers(body), SECRET)
    assert caught.value.status_code == 400


def test_hmac_uses_original_unicode_and_whitespace_bytes():
    data = payload(extra="Ελληνικά 🐙")
    body = json.dumps(data, indent=2, ensure_ascii=False).encode()
    assert NotionProvider().decode(body, headers(body), SECRET).event.payload == data
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(body + b" ", headers(body), SECRET)
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "signature", ["", "sha256=abc", "sha256=" + "0" * 64, "sha256=" + "z" * 64, "sha256=é", "x,x"]
)
def test_invalid_signature_before_json(signature):
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(b"not json", {"x-notion-signature": signature}, SECRET)
    assert caught.value.status_code == 401


def test_wrong_secret_media_type_and_unsigned_bootstrap():
    body = json.dumps(payload()).encode()
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(body, headers(body), "another")
    assert caught.value.status_code == 401
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(body, {**headers(body), "content-type": "text/plain"}, SECRET)
    assert caught.value.status_code == 415
    with pytest.raises(WebhookError) as caught:
        NotionProvider().decode(b'{"verification_token":"candidate"}', {}, SECRET)
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"event_type": "page"},
        {"event_type": "page.*"},
        {"event_type": "*", "page_id": ENTITY},
        {"event_type": "*", "entity_type": "*"},
        {"event_type": "*", "workspace_id": "https://notion.so/x"},
    ],
)
def test_registration_rejects_unsupported_configuration(config):
    with pytest.raises(ValueError):
        NotionProvider().validate("event", config)


def test_registration_action_and_all_supported_filters():
    provider = NotionProvider()
    with pytest.raises(ValueError):
        provider.validate("page_created", {"event_type": "*"})
    event = decode(payload())
    config = {
        name: getattr(event, name)
        for name in (
            "event_type",
            "workspace_id",
            "integration_id",
            "subscription_id",
            "entity_id",
            "entity_type",
        )
    }
    assert provider.matches(provider.validate("event", config), event)


@pytest.mark.parametrize("budget", [True, 0, -1, float("inf"), float("nan"), "5"])
def test_invalid_request_timeout(budget):
    with pytest.raises(ValueError):
        NotionProvider(request_timeout=budget)


def test_codec_rejects_forged_context_and_does_not_import_types():
    codec = NotionProvider().event_codec
    event = decode(payload())
    for changed in (
        replace(event, delivery_id="fake"),
        replace(event, event_type="page.deleted"),
        replace(event, provider="github"),
        replace(event, attempt_number=True),
    ):
        with pytest.raises(ValueError):
            codec.encode(changed)
    with pytest.raises(ValueError):
        codec.encode(HookEvent("notion", "event", "1"))
    data = asdict(event)
    data["module"] = "untrusted.module"
    with pytest.raises(ValueError):
        codec.decode(data)
