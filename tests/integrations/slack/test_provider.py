"""Authentication, normalization, and selectors through Slack's public adapter."""

import json
from dataclasses import FrozenInstanceError

import pytest

from anyo.hooks import HookResponse, Provider, ResponseProvider, WebhookError
from anyo.integrations.slack import SlackEvent, SlackProvider

from .conftest import (
    NOW,
    SECRET,
    command_payload,
    encoded,
    event_payload,
    interaction_payload,
    signed,
)


def decode(provider, data, kind="json", **headers):
    body, content_type = encoded(data, kind)
    return provider.decode(body, signed(body, content_type, **headers), SECRET)


def match(provider, event, action="event", **configuration):
    config = provider.validate(action, {"app_id": "A123", "team_id": "T123", **configuration})
    return provider.matches(config, event)


def test_public_provider_and_slack_event_contract(provider):
    typed: Provider = provider
    assert isinstance(typed, ResponseProvider)
    payload = event_payload(
        "reaction_added",
        user="U123",
        reaction="eyes",
        item={"type": "message", "channel": "C123", "ts": "123.000001"},
    )
    delivery = decode(provider, payload)
    event = delivery.event
    assert isinstance(event, SlackEvent)
    assert event.action == "event"
    assert event.event_type == "reaction_added"
    assert (event.app_id, event.team_id, event.channel_id, event.user_id) == (
        "A123",
        "T123",
        "C123",
        "U123",
    )
    assert event.organization == event.repository == ""
    assert event.message_ts == "123.000001"
    assert event.payload == payload
    assert delivery.response.status_code == 200
    assert delivery.response.body == b""
    assert match(provider, event, event_type="reaction_added", channel_id="C123", reaction="eyes")
    assert not match(provider, event, event_type="reaction_removed")
    assert not match(provider, event, team_id="T999")
    assert "item" not in repr(event)
    with pytest.raises(FrozenInstanceError):
        event.app_id = "A999"


@pytest.mark.parametrize("timestamp", [NOW - 300, NOW, NOW + 300])
def test_signature_window_boundaries(provider, timestamp):
    assert decode(provider, event_payload(), timestamp=timestamp).event is not None


def test_rate_limit_identity_requires_workspace(provider):
    with pytest.raises(WebhookError, match="team_id"):
        decode(
            provider,
            {
                "type": "app_rate_limited",
                "api_app_id": "A123",
                "enterprise_id": "E123",
                "minute_rate_limited": 123,
            },
        )


@pytest.mark.parametrize("timestamp", [NOW - 301, NOW + 301, -1, "garbage", "0" * 20])
def test_rejects_stale_future_and_malformed_timestamps(provider, timestamp):
    with pytest.raises(WebhookError) as error:
        decode(provider, event_payload(), timestamp=timestamp)
    assert error.value.status_code == 401


def test_signature_precedes_parsing_and_tampering_is_rejected(provider):
    body = b"not json"
    with pytest.raises(WebhookError) as error:
        provider.decode(body, {"content-type": "application/json"}, SECRET)
    assert error.value.status_code == 401
    for headers in [
        signed(body, secret="wrong"),
        signed(body + b" "),
        {**signed(body), "x-slack-signature": "v1=" + "a" * 64},
    ]:
        with pytest.raises(WebhookError) as error:
            provider.decode(body, headers, SECRET)
        assert error.value.status_code == 401


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"[]", "application/json"),
        (b"{", "application/json"),
        (b'{"type":"event_callback","type":"url_verification"}', "application/json"),
        (b'{"value":NaN}', "application/json"),
        (b"\xff", "application/json"),
        (b"payload=%GG", "application/x-www-form-urlencoded"),
        (b"command=%FF", "application/x-www-form-urlencoded"),
        (b"command=/a&command=/b", "application/x-www-form-urlencoded"),
        (b"payload={}&command=/a", "application/x-www-form-urlencoded"),
        (b"payload=[]", "application/x-www-form-urlencoded"),
        (b"{}", "text/plain"),
    ],
)
def test_malformed_or_ambiguous_signed_requests(provider, body, content_type):
    with pytest.raises(WebhookError) as error:
        provider.decode(body, signed(body, content_type), SECRET)
    assert error.value.status_code in {400, 415}


@pytest.mark.parametrize(
    "data,kind",
    [
        ({}, "json"),
        (event_payload(event_id=None), "json"),
        ({"type": "event_callback", "api_app_id": "A123", "event_id": "Ev", "event": {}}, "json"),
        (interaction_payload(actions=[]), "interaction"),
        (interaction_payload(actions="bad"), "interaction"),
        (interaction_payload("view_submission", view=[]), "interaction"),
        (command_payload(command="anyo"), "form"),
        (
            {
                "type": "app_rate_limited",
                "api_app_id": "A123",
                "team_id": "T123",
                "minute_rate_limited": True,
            },
            "json",
        ),
    ],
)
def test_required_envelope_fields(provider, data, kind):
    # Remove the real outer identity for this one deliberately malformed case.
    if "event_id" in data.get("event", {}):
        data["event_id"] = data["event"].pop("event_id")
    with pytest.raises(WebhookError) as error:
        decode(provider, data, kind)
    assert error.value.status_code == 400


def test_form_encoding_and_unicode_are_verified_as_received(provider):
    command = command_payload(text="café + & = / 🚀")
    event = decode(provider, command, "form").event
    assert event.payload == command
    assert event.action == "slash_command"
    assert match(provider, event, action="slash_command", command="/anyo")
    assert not match(provider, event, action="slash_command", command="/other")


def test_setup_challenge_and_signed_probe_do_not_dispatch(provider):
    delivery = decode(provider, {"type": "url_verification", "challenge": "challenge-secret"})
    assert delivery.event is None
    assert json.loads(delivery.response.body) == {"challenge": "challenge-secret"}
    assert not delivery.response.dispatch
    probe = decode(provider, {"ssl_check": "1", "token": "deprecated"}, "form")
    assert probe.event is None
    assert probe.response.body == b""
    assert not probe.response.dispatch


def test_rate_limited_has_its_own_stable_identity(provider):
    payload = {
        "type": "app_rate_limited",
        "api_app_id": "A123",
        "team_id": "T123",
        "minute_rate_limited": NOW // 60 * 60,
    }
    a = decode(provider, payload).event
    b = decode(provider, payload, timestamp=NOW + 1).event
    assert a.delivery_id == b.delivery_id
    assert a.action == "control"
    assert match(provider, a, action="control", event_type="app_rate_limited")


def test_future_native_type_preserves_unknown_fields_and_missing_optional_context(provider):
    data = event_payload("future_event", opaque={"new": [1, None, True]})
    event = decode(provider, data).event
    assert event.payload == data
    assert event.channel_id is event.user_id is None
    assert match(provider, event, event_type="future_event")
    assert match(provider, event, event_type="*")


def test_message_alias_subtype_and_nested_edit_actor(provider):
    data = event_payload(
        "message",
        subtype="message_changed",
        channel="C123",
        channel_type="channel",
        hidden=True,
        message={
            "user": "UAUTHOR",
            "edited": {"user": "UEDITOR"},
            "ts": "100.0001",
            "thread_ts": "99.0001",
        },
    )
    event = decode(provider, data).event
    assert event.user_id == "UEDITOR"
    assert event.thread_ts == "99.0001"
    assert match(provider, event, event_type="message.channels", subtype="message_changed")
    assert not match(provider, event, event_type="message.im")
    assert not match(provider, event, subtype="")
    plain = decode(provider, event_payload()).event
    assert match(provider, plain, subtype="")


def test_org_install_and_shared_channel_route_receiving_installation(provider):
    data = event_payload("app_mention", channel="CSHARED", user="UREMOTE")
    data.update(team_id="TREMOTE", context_team_id="TLOCAL", context_enterprise_id="ELOCAL")
    event = decode(provider, data).event
    assert event.team_id == "TREMOTE"
    assert match(provider, event, team_id="TLOCAL", enterprise_id="ELOCAL")
    assert not match(provider, event, team_id="TREMOTE")
    data.pop("context_team_id")
    data.pop("context_enterprise_id")
    data["authorizations"] = [
        {"team_id": "TONE", "enterprise_id": "EONE"},
        {"team_id": "TTWO", "enterprise_id": "ETWO"},
    ]
    event = decode(provider, data).event
    assert not match(provider, event, team_id="TONE", enterprise_id="ETWO")
    assert match(provider, event, team_id="TTWO", enterprise_id="ETWO")
    org = decode(
        provider, interaction_payload(team=None, enterprise={"id": "EORG"}), "interaction"
    ).event
    config = provider.validate("interaction", {"app_id": "A123", "enterprise_id": "EORG"})
    assert provider.matches(config, org)
    assert not match(provider, org, action="interaction", team_id="*")


def test_action_and_block_filters_must_match_the_same_action(provider):
    data = interaction_payload(
        actions=[
            {"action_id": "approve", "block_id": "one"},
            {"action_id": "reject", "block_id": "two"},
        ]
    )
    event = decode(provider, data, "interaction").event
    assert match(provider, event, action="interaction", action_id="approve", block_id="one")
    assert not match(provider, event, action="interaction", action_id="approve", block_id="two")
    assert len(event.actions) == 2


def test_bot_and_self_filtering_is_explicit(provider):
    event = decode(
        provider,
        event_payload(
            "message",
            subtype="bot_message",
            bot_id="B123",
            user="UBOT",
            bot_profile={"app_id": "A123"},
        ),
    ).event
    assert match(provider, event)
    assert not match(provider, event, include_bots=False)
    assert not match(provider, event, ignore_self=True)
    assert not match(provider, event, ignore_user_id="UBOT")
    assert match(provider, event, ignore_user_id="UOTHER")


def test_retry_headers_do_not_create_new_deliveries(provider):
    for data, kind in [
        (event_payload(), "json"),
        (command_payload(), "form"),
        (interaction_payload(), "interaction"),
    ]:
        one = decode(provider, data, kind).event
        two = decode(
            provider,
            data,
            kind,
            timestamp=NOW + 3,
            **{"x-slack-retry-num": "1", "x-slack-retry-reason": "http_timeout"},
        ).event
        assert one.delivery_id == two.delivery_id
        assert two.retry_num == 1
        assert two.retry_reason == "http_timeout"
    for data, kind in [(command_payload(), "form"), (interaction_payload(), "interaction")]:
        one = decode(provider, data, kind).event
        data["trigger_id"] = "invocation-2"
        assert decode(provider, data, kind).event.delivery_id != one.delivery_id


def test_identity_without_invocation_metadata_uses_signed_second(provider):
    data = interaction_payload("view_closed")
    del data["trigger_id"]
    one = decode(provider, data, "interaction").event
    assert decode(provider, data, "interaction").event.delivery_id == one.delivery_id
    assert (
        decode(provider, data, "interaction", timestamp=NOW + 1).event.delivery_id
        != one.delivery_id
    )


def test_legacy_missing_app_id_requires_explicit_secret_binding(provider):
    data = interaction_payload("interactive_message", actions=[{"name": "approve"}])
    del data["api_app_id"]
    event = decode(provider, data, "interaction").event
    assert not match(provider, event, action="interaction", interaction_type="interactive_message")
    assert match(
        provider,
        event,
        action="interaction",
        interaction_type="interactive_message",
        allow_legacy_app_id=True,
    )


@pytest.mark.parametrize(
    "configuration",
    [
        {},
        {"app_id": "A123"},
        {"team_id": "T123"},
        {"app_id": "*", "team_id": "T123"},
        {"app_id": "A123", "team_id": "T123", "unknown": "yes"},
        {"app_id": "A123", "team_id": "T123", "include_bots": "false"},
        {"app_id": "A123", "team_id": " T123"},
        {"app_id": "A123", "team_id": "T123", "event_type": "bad type"},
        {
            "app_id": "A123",
            "team_id": "T123",
            "event_type": "message.im",
            "channel_type": "channel",
        },
    ],
)
def test_invalid_configuration(provider, configuration):
    with pytest.raises(ValueError):
        provider.validate("event", configuration)


@pytest.mark.parametrize(
    "event_type",
    [
        "block_actions",
        "shortcut",
        "message_action",
        "view_submission",
        "view_closed",
        "dialog_submission",
        "dialog_cancellation",
    ],
)
def test_interaction_types_and_empty_acknowledgment(provider, event_type):
    delivery = decode(provider, interaction_payload(event_type), "interaction")
    assert delivery.event.event_type == event_type
    assert delivery.render(HookResponse()).body == b""


@pytest.mark.parametrize("timeout", [0, -1, 3, 5, True, float("nan")])
def test_invalid_response_timeout(timeout):
    with pytest.raises(ValueError):
        SlackProvider(request_timeout=timeout)


@pytest.mark.parametrize("fragment", [b'"\\ud800"', b"1e999"])
def test_unencodable_json_is_rejected_at_authenticated_boundary(provider, fragment):
    raw = b'{"type":"event_callback","extra":' + fragment + b"}"
    with pytest.raises(WebhookError) as error:
        provider.decode(raw, signed(raw), SECRET)
    assert error.value.status_code == 400


def test_enterprise_context_from_team_and_matching_authorization(provider):
    data = interaction_payload("block_suggestion", team={"id": "T123", "enterprise_id": "E123"})
    event = decode(provider, data, "interaction").event
    assert match(provider, event, action="interaction", enterprise_id="E123")
    data = event_payload()
    data.update(
        context_team_id="TLOCAL", authorizations=[{"team_id": "TLOCAL", "enterprise_id": "ELOCAL"}]
    )
    event = decode(provider, data).event
    assert match(provider, event, team_id="TLOCAL", enterprise_id="ELOCAL")


def test_ignore_self_does_not_drop_app_lifecycle_events(provider):
    event = decode(provider, event_payload("app_installed", app_id="A123")).event
    assert match(provider, event, ignore_self=True)
