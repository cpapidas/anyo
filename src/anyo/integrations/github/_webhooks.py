"""GitHub webhook authentication and pull-request event translation."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import NoReturn

from anyo.hooks import HookEvent, WebhookError
from anyo.integrations.github._configuration import normalize_name

_DELIVERY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SIGNATURE = re.compile(r"sha256=[0-9a-fA-F]{64}")
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


def decode_webhook(
    body: bytes, headers: Mapping[str, str], secret: str, *, provider_name: str
) -> HookEvent | None:
    """Verify the original body before parsing any event or routing fields."""
    signature = headers.get("x-hub-signature-256", "")
    if not _SIGNATURE.fullmatch(signature):
        raise WebhookError(401, "Invalid webhook signature")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:].lower(), expected):
        raise WebhookError(401, "Invalid webhook signature")

    delivery_id = headers.get("x-github-delivery", "")
    if not _DELIVERY.fullmatch(delivery_id):
        raise WebhookError(400, "Missing or invalid GitHub delivery ID")
    event_name = headers.get("x-github-event", "")
    if not _EVENT.fullmatch(event_name):
        raise WebhookError(400, "Missing or invalid GitHub event header")
    try:
        decoded: object = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WebhookError(400, "Invalid JSON webhook body") from exc
    payload = _object(decoded, "body")

    if event_name != "pull_request":
        return None
    action = payload.get("action")
    if not isinstance(action, str) or not _EVENT.fullmatch(action):
        raise WebhookError(400, "Missing or invalid pull request action")
    if action != "opened":
        return None

    repository = _object(payload.get("repository"), "repository")
    owner = _object(repository.get("owner"), "repository owner")
    try:
        organization_name = normalize_name(owner.get("login"), "organization")
        repository_name = normalize_name(repository.get("name"), "repository")
    except ValueError as exc:
        raise WebhookError(400, "Invalid target repository") from exc

    number = payload.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise WebhookError(400, "Missing or invalid pull request number")
    pull_request = _object(payload.get("pull_request"), "pull request")
    head = _object(pull_request.get("head"), "pull request head")
    sha = head.get("sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise WebhookError(400, "Missing or invalid pull request head SHA")

    return HookEvent(
        provider=provider_name,
        action="pr_created",
        delivery_id=delivery_id,
        organization=organization_name,
        repository=repository_name,
        payload=payload,
        pull_request_number=number,
        head_sha=sha.lower(),
    )


def _object(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise WebhookError(400, f"Missing or invalid webhook {field}")
    return value


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-JSON numeric constant")
