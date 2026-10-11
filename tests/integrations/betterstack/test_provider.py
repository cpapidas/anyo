"""Authentication, native payload mapping, filters, and identity limits."""

import copy
import json
from dataclasses import asdict

import pytest

from anyo.execution._codec import EventCodecRegistry
from anyo.hooks import Provider, ResponseProvider, WebhookError
from anyo.integrations.betterstack import BetterStackEvent, BetterStackProvider

from .conftest import HEADERS, SECRET, error, incident


def decode(provider, payload):
    return provider.decode(json.dumps(payload).encode(), HEADERS, SECRET).event


@pytest.mark.parametrize(
    "kind", ["started", "acknowledged", "resolved", "reopened", "comment", "future_event", None]
)
def test_incident_lifecycle_and_absent_discriminator(provider, kind):
    payload = incident(kind)
    event = decode(provider, payload)
    assert isinstance(event, BetterStackEvent)
    assert event.event_type == (kind or "unspecified")
    assert event.action == "incident" and event.resource_id == "12"
    assert event.payload == payload
    assert event.repository == event.organization == ""
    assert event.monitor_id is None and event.application_id is None
    assert event.comment_id == ("19" if kind == "comment" else None)


@pytest.mark.parametrize(
    "kind",
    [
        "new_error",
        "threshold",
        "spike",
        "reoccurred",
        "resolved",
        "ignored",
        "unresolved",
        "future_event",
    ],
)
def test_error_lifecycle_and_context(provider, kind):
    event = decode(provider, error(kind, notify_threshold=10, previous_state="resolved"))
    assert event.action == "error" and event.event_type == kind
    assert event.application_id == "4821" and event.team_id == "12345"
    assert event.resource_id == "abc123" and event.environment == "production"
    assert event.first_seen == "2026-10-11T12:00:00Z"
    assert event.source_url.startswith("https://errors.betterstack.com/")
    assert "Private application message" not in repr(event)
    assert "production" not in repr(event)


def test_monitor_context_and_null_relationship(provider):
    data = incident()
    data["data"]["relationships"] = {"monitor": {"data": {"id": 9, "type": "monitor"}}}
    assert decode(provider, data).monitor_id == "9"
    data["data"]["relationships"]["monitor"]["data"] = None
    assert decode(provider, data).monitor_id is None


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**HEADERS, "x-anyo-webhook-secret": "bad"},
        {**HEADERS, "x-anyo-webhook-secret": SECRET + ", " + SECRET},
        {**HEADERS, "x-anyo-webhook-secret": "\u2603"},
    ],
)
def test_authentication_precedes_json_decoding(provider, headers):
    with pytest.raises(WebhookError) as caught:
        provider.decode(b"not json", headers, SECRET)
    assert caught.value.status_code == 401
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b"{}",
        b"\xff",
        b'{"a":NaN}',
        b'{"a":1e999}',
        b'{"data":{},"data":{}}',
        b'{"data":{"type":"incident","type":"error"}}',
        b'{"a":"\\ud800"}',
        b"[" * 70 + b"0" + b"]" * 70,
    ],
)
def test_invalid_json_fails_safely(provider, body):
    with pytest.raises(WebhookError) as caught:
        provider.decode(body, HEADERS, SECRET)
    assert caught.value.status_code == 400
    assert str(caught.value) == "Invalid Better Stack webhook body"


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["data"].update(id=True),
        lambda p: p["data"].update(id=[]),
        lambda p: p["data"].update(attributes=[]),
        lambda p: p.update(event=None),
        lambda p: p.update(event="Bad Event"),
        lambda p: p["data"].update(relationships={"monitor": {"data": {"id": 2, "type": "team"}}}),
        lambda p: p["data"].update(relationships={"monitor": {}}),
        lambda p: p.update(event="comment"),
        lambda p: p["data"]["attributes"].update(started_at=123),
    ],
)
def test_malformed_supported_incident(provider, change):
    payload = incident()
    change(payload)
    with pytest.raises(WebhookError) as caught:
        decode(provider, payload)
    assert caught.value.status_code == 400


@pytest.mark.parametrize(
    "fields",
    [
        {"event": None},
        {"application_id": False},
        {"team_id": []},
        {"environment": 42},
        {"error_url": {}},
    ],
)
def test_malformed_error_context(provider, fields):
    with pytest.raises(WebhookError):
        decode(provider, error(**fields))


@pytest.mark.parametrize(
    "data",
    [
        {"data": {"type": "release"}},
        {"data": {"type": "monitor"}},
        {"data": {"calendar_id": 1, "message": "On call"}},
    ],
)
def test_unsupported_families_are_ignored(provider, data):
    assert decode(provider, data) is None


def test_content_type_and_charset(provider):
    with pytest.raises(WebhookError) as caught:
        provider.decode(b"{}", {**HEADERS, "content-type": "text/plain"}, SECRET)
    assert caught.value.status_code == 415
    assert provider.decode(
        json.dumps(error()).encode(),
        {**HEADERS, "content-type": "application/json; charset=utf-8"},
        SECRET,
    ).event


@pytest.mark.parametrize(
    "action,config",
    [
        ("release", {"event_type": "*"}),
        ("incident", {}),
        ("incident", {"event_type": "*", "application_id": "1"}),
        ("incident", {"event_type": "*", "team_id": "1"}),
        ("error", {"event_type": "*", "monitor_id": "1"}),
        ("error", {"event_type": "*", "application_id": True}),
        ("error", {"event_type": "*", "environment": " production"}),
        ("error", {"event_type": "New Error"}),
        ("error", {"event_type": "*", "team_id": "*"}),
    ],
)
def test_bad_registration_fails_locally(provider, action, config):
    with pytest.raises(ValueError):
        provider.validate(action, config)


def test_exact_filters_and_missing_context(provider):
    config = provider.validate(
        "error",
        {
            "event_type": "new_error",
            "application_id": 4821,
            "environment": "production",
            "team_id": "12345",
        },
    )
    assert config["application_id"] == "4821"
    assert provider.matches(config, decode(provider, error()))
    for fields in (
        {"application_id": None},
        {"team_id": 7},
        {"environment": None},
        {"environment": "Production"},
        {"event": "resolved"},
    ):
        assert not provider.matches(config, decode(provider, error(**fields)))
    assert provider.matches(
        provider.validate("incident", {"event_type": "*"}), decode(provider, incident(None))
    )
    assert not provider.matches({"monitor_id": "9"}, decode(provider, incident()))


def test_identity_is_canonical_and_preserves_distinguishable_transitions(provider):
    payload = error()
    first = decode(provider, payload)
    reordered = json.dumps(payload, sort_keys=True, indent=4).encode()
    assert provider.decode(reordered, HEADERS, SECRET).event.delivery_id == first.delivery_id
    assert decode(provider, incident()).delivery_id != first.delivery_id
    for kind in ("threshold", "spike", "reoccurred", "resolved"):
        one = decode(provider, error(kind, last_seen="2026-10-11T12:01:00Z"))
        two = decode(provider, error(kind, last_seen="2026-10-11T12:02:00Z"))
        assert one.delivery_id != two.delivery_id
    comment = incident("comment")
    a = decode(provider, comment)
    comment["comment"]["id"] = "20"
    assert decode(provider, comment).delivery_id != a.delivery_id
    # Documented limitation: identical distinct occurrences cannot be told apart;
    # changed retry bodies also change identity. No imagined upstream delivery ID.
    assert decode(provider, payload).delivery_id == first.delivery_id
    payload["retry_count"] = 1
    assert decode(provider, payload).delivery_id != first.delivery_id


def test_versioned_codec_roundtrip_and_corruption_rejection(provider):
    codecs = EventCodecRegistry([provider.event_codec])
    event = decode(provider, error())
    clone = codecs.decode(codecs.encode(event))
    assert clone == event and type(clone) is BetterStackEvent
    assert clone.payload is not event.payload
    for change in (
        {"provider": "other"},
        {"application_id": "different"},
        {"delivery_id": "forged"},
        {"resource_id": None},
        {"unexpected": 1},
    ):
        data = asdict(event)
        data.update(change)
        with pytest.raises(ValueError):
            provider.event_codec.decode(data)
    data = copy.deepcopy(asdict(event))
    data["payload"]["bad"] = float("nan")
    with pytest.raises(ValueError):
        provider.event_codec.decode(data)


@pytest.mark.parametrize("timeout", [0, -1, 25, float("nan"), float("inf"), True])
def test_timeout_validation(timeout):
    with pytest.raises(ValueError):
        BetterStackProvider(request_timeout=timeout)


def test_structural_contract(provider):
    adapter: Provider = provider
    assert isinstance(adapter, ResponseProvider)
    assert adapter.secret_env == "ANYO_BETTERSTACK_WEBHOOK_SECRET"
