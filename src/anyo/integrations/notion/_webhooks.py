"""Raw-byte authentication, portable JSON, and native envelope validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import NoReturn
from uuid import UUID

from anyo.hooks import WebhookError
from anyo.integrations.notion._events import NotionEvent

NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
UUID_TEXT = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")
SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")


def identifier(value: object) -> str:
    if not isinstance(value, str) or not UUID_TEXT.fullmatch(value):
        raise ValueError("Invalid Notion UUID identifier")
    return str(UUID(value))


def event_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > 255 or not EVENT_NAME.fullmatch(value):
        raise ValueError("Invalid Notion event type")
    return value


def entity_name(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Invalid Notion entity type")
    return value


def validate_json(value: object, depth: int = 0) -> None:
    """Enforce the same finite JSON boundary for memory and durable execution."""
    if depth > 64:
        raise ValueError("Notion JSON nesting exceeds 64 levels")
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        value.encode("utf-8")
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            validate_json(key, depth + 1)
            validate_json(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            validate_json(item, depth + 1)
        return
    raise ValueError("Notion payload must contain finite JSON values")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise ValueError("Invalid JSON constant")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected Notion JSON object")
    return value


def parse_object(body: bytes) -> dict[str, object]:
    result = _object(
        json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    )
    validate_json(result)
    return result


def _string(value: object, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Invalid Notion context string")
    return value


def _actors(value: object) -> None:
    if not isinstance(value, list):
        raise ValueError("Invalid Notion actors")
    for item in value:
        actor = _object(item)
        identifier(actor.get("id"))
        entity_name(actor.get("type"))


def event_from_payload(payload: dict[str, object]) -> NotionEvent:
    """Derive only source-backed context; never infer parents or entity types."""
    if "verification_token" in payload:
        raise ValueError("Verification is only supported by the setup command")
    event_id = _string(payload.get("id"), 512)
    native = event_name(payload.get("type"))
    workspace = identifier(payload.get("workspace_id"))
    integration = identifier(payload.get("integration_id"))
    subscription = identifier(payload.get("subscription_id"))
    entity = _object(payload.get("entity"))
    entity_id = identifier(entity.get("id"))
    kind = entity_name(entity["type"]) if "type" in entity else None
    timestamp = _string(payload.get("timestamp"), 64)
    parsed = datetime.fromisoformat(timestamp)
    if "T" not in timestamp or parsed.tzinfo is None:
        raise ValueError("Notion timestamp must include time and timezone")
    attempt = payload.get("attempt_number")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("Invalid Notion delivery attempt")
    version = None
    if "api_version" in payload:
        version = _string(payload["api_version"], 10)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
            raise ValueError("Invalid Notion API version")
        date.fromisoformat(version)
    _actors(payload.get("authors"))
    if "accessible_by" in payload:
        _actors(payload["accessible_by"])
    if "workspace_name" in payload:
        # Names are private payload data, not loggable derived context.
        if not isinstance(payload["workspace_name"], str):
            raise ValueError("Invalid Notion workspace name")
    if "data" in payload:
        _object(payload["data"])
    identity = json.dumps([workspace, integration, subscription, event_id], ensure_ascii=True)
    return NotionEvent(
        provider="notion",
        action="event",
        delivery_id="notion:event:v1:" + hashlib.sha256(identity.encode()).hexdigest(),
        payload=payload,
        event_id=event_id,
        event_type=native,
        workspace_id=workspace,
        integration_id=integration,
        subscription_id=subscription,
        entity_id=entity_id,
        entity_type=kind,
        timestamp=timestamp,
        attempt_number=attempt,
        api_version=version,
    )


def decode(body: bytes, headers: Mapping[str, str], secret: str) -> NotionEvent:
    supplied = headers.get("x-notion-signature", "")
    if not SIGNATURE.fullmatch(supplied) or not hmac.compare_digest(
        supplied, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ):
        raise WebhookError(401, "Invalid webhook authentication")
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise WebhookError(415, "Notion requires application/json")
    try:
        return event_from_payload(parse_object(body))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WebhookError(400, "Invalid Notion webhook body") from exc
