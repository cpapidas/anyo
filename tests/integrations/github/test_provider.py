"""Contract tests for authenticated GitHub event translation."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from anyo.hooks import HookEvent, Provider, WebhookError
from anyo.integrations.github import GitHubProvider

SECRET = "a local test webhook secret"
DELIVERY = "18c644a0-57f0-11ef-a424-325096b39f12"
SHA = "abcdef0123" * 4


def payload() -> dict[str, object]:
    return {
        "action": "opened",
        "number": 42,
        "repository": {"owner": {"login": "Target-Owner"}, "name": "Target_Repo"},
        "pull_request": {
            "head": {
                "sha": SHA,
                "repo": {"owner": {"login": "Fork-Owner"}, "name": "Other_Repo"},
            }
        },
    }


def signed_headers(body: bytes, *, event: str = "pull_request") -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": f"sha256={digest}",
        "x-github-delivery": DELIVERY,
        "x-github-event": event,
    }


def decode(data: object, *, event: str = "pull_request") -> HookEvent | None:
    body = json.dumps(data).encode()
    provider: Provider = GitHubProvider()
    return provider.decode(body, signed_headers(body, event=event), SECRET)


def test_normalizes_configuration_without_mutating_input() -> None:
    original: Mapping[str, object] = {
        "organization": "Personal-Account",
        "repository": "My.Repo_v2",
    }
    assert GitHubProvider().validate("pr_created", original) == {
        "organization": "personal-account",
        "repository": "my.repo_v2",
    }
    assert original["organization"] == "Personal-Account"


@pytest.mark.parametrize("action", ["", "opened", "pull_request", "pr_updated"])
def test_rejects_unsupported_action(action: str) -> None:
    with pytest.raises(ValueError, match="pr_created"):
        GitHubProvider().validate(action, {"organization": "o", "repository": "r"})


@pytest.mark.parametrize(
    "configuration",
    [
        {},
        {"organization": "o"},
        {"repository": "r"},
        {"organization": "o", "repository": "r", "branch": "main"},
        {"organization": "", "repository": "r"},
        {"organization": " owner", "repository": "r"},
        {"organization": "owner/name", "repository": "r"},
        {"organization": "o", "repository": "o/r"},
        {"organization": "o", "repository": ""},
        {"organization": "o", "repository": "."},
        {"organization": "o", "repository": ".."},
        {"organization": "o", "repository": "r\n"},
        {"organization": None, "repository": "r"},
        {"organization": "o", "repository": 1},
        {"organization": "a" * 40, "repository": "r"},
        {"organization": "o", "repository": "a" * 101},
    ],
)
def test_rejects_invalid_configuration(configuration: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GitHubProvider().validate("pr_created", configuration)


def test_opened_event_uses_target_repository_and_exposes_payload() -> None:
    source = payload()
    event = decode(source)
    assert event is not None
    assert event.provider == "github"
    assert event.action == "pr_created"
    assert event.delivery_id == DELIVERY
    assert event.organization == "target-owner"
    assert event.repository == "target_repo"
    assert event.pull_request_number == 42
    assert event.head_sha == SHA
    assert event.payload == source
    assert "payload=" not in repr(event)
    with pytest.raises(FrozenInstanceError):
        setattr(event, "repository", "changed")

    provider = GitHubProvider()
    target = provider.validate(
        "pr_created", {"organization": "TARGET-OWNER", "repository": "Target_Repo"}
    )
    assert provider.matches(target, event)
    assert not provider.matches({"organization": "fork-owner", "repository": "other_repo"}, event)
    assert not provider.matches({"organization": "target-owner", "repository": "different"}, event)


@pytest.mark.parametrize("event_name", ["ping", "push", "issues", "unknown_event"])
def test_authentic_irrelevant_events_are_ignored(event_name: str) -> None:
    assert decode({}, event=event_name) is None


@pytest.mark.parametrize("action", ["closed", "reopened", "synchronize", "edited"])
def test_other_pull_request_actions_are_ignored(action: str) -> None:
    assert decode({"action": action}) is None


@pytest.mark.parametrize(
    "signature",
    [None, "", "sha1=" + "0" * 40, "sha256=" + "0" * 64, "sha256=é", "😺"],
)
def test_invalid_signature_is_rejected_before_json_parsing(
    signature: str | None,
) -> None:
    body = b"not json"
    headers = signed_headers(body)
    if signature is None:
        del headers["x-hub-signature-256"]
    else:
        headers["x-hub-signature-256"] = signature
    with pytest.raises(WebhookError, match="signature") as raised:
        GitHubProvider().decode(body, headers, SECRET)
    assert raised.value.status_code == 401
    assert SECRET not in str(raised.value)


def test_signature_covers_original_bytes_and_secret() -> None:
    body = json.dumps(payload()).encode()
    provider = GitHubProvider()
    for actual_body, actual_secret in [(body + b" ", SECRET), (body, "another secret")]:
        with pytest.raises(WebhookError) as raised:
            provider.decode(actual_body, signed_headers(body), actual_secret)
        assert raised.value.status_code == 401


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("x-github-delivery", None),
        ("x-github-delivery", ""),
        ("x-github-delivery", "bad\nvalue"),
        ("x-github-delivery", "a" * 129),
        ("x-github-event", None),
        ("x-github-event", ""),
        ("x-github-event", "pull_request\r\n"),
        ("x-github-event", "a" * 65),
    ],
)
def test_rejects_missing_or_malformed_identity_headers(header: str, value: str | None) -> None:
    body = json.dumps(payload()).encode()
    headers = signed_headers(body)
    if value is None:
        del headers[header]
    else:
        headers[header] = value
    with pytest.raises(WebhookError) as raised:
        GitHubProvider().decode(body, headers, SECRET)
    assert raised.value.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff",
        b"[]",
        b"null",
        b'"string"',
        b'{"value": NaN}',
        b'{"value": Infinity}',
        b'{"value": -Infinity}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_malformed_signed_json_is_a_safe_client_error(body: bytes) -> None:
    with pytest.raises(WebhookError) as raised:
        GitHubProvider().decode(body, signed_headers(body), SECRET)
    assert raised.value.status_code == 400
    assert SECRET not in str(raised.value)
    assert str(body) not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", None),
        ("action", []),
        ("action", ""),
        ("repository", None),
        ("repository", []),
        ("repository", {"name": "r", "owner": None}),
        ("repository", {"name": "r", "owner": {"login": ""}}),
        ("repository", {"name": "o/r", "owner": {"login": "o"}}),
        ("number", None),
        ("number", True),
        ("number", 0),
        ("number", -1),
        ("number", "42"),
        ("number", 1.2),
        ("pull_request", None),
        ("pull_request", []),
        ("pull_request", {"head": None}),
        ("pull_request", {"head": {"sha": None}}),
        ("pull_request", {"head": {"sha": "a" * 39}}),
        ("pull_request", {"head": {"sha": "z" * 40}}),
    ],
)
def test_rejects_incomplete_or_malformed_supported_event(field: str, value: object) -> None:
    data = payload()
    data[field] = value
    with pytest.raises(WebhookError) as raised:
        decode(data)
    assert raised.value.status_code == 400


@pytest.mark.parametrize("sha", ["A" * 40, "B" * 64])
def test_accepts_supported_hex_sha_lengths(sha: str) -> None:
    data = payload()
    data["pull_request"] = {"head": {"sha": sha}}
    event = decode(data)
    assert event is not None
    assert event.head_sha == sha.lower()
