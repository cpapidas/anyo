"""Strict local decoding and deterministic, bounded-history delivery identity."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from typing import NoReturn

from anyo.hooks import WebhookError
from anyo.integrations.betterstack._events import BetterStackEvent

NAME = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")


def identifier(value: object) -> str:
    if type(value) is int and value > 0:
        value = str(value)
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid Better Stack identifier")
    return value


def validate_json(value: object, depth: int = 0) -> None:
    """Reject ambiguous/nonportable JSON even for the memory execution path."""
    if depth > 64:
        raise ValueError("Better Stack JSON nesting exceeds 64 levels")
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        value.encode("utf-8")  # Reject unpaired surrogates before hashing/storage.
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
    raise ValueError("Better Stack payload must contain finite JSON values")


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
        raise ValueError("Expected Better Stack JSON object")
    return value


def _optional_string(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid Better Stack context string")
    return value


def _optional_id(value: object) -> str | None:
    return None if value is None else identifier(value)


def decode(body: bytes, headers: Mapping[str, str], secret: str) -> BetterStackEvent | None:
    supplied = headers.get("x-anyo-webhook-secret", "")
    # Commas cannot be credentials: the receiver combines repeated headers.
    if (
        not supplied
        or len(supplied) > 4096
        or any(ord(c) < 33 or ord(c) > 126 or c == "," for c in supplied)
        or not hmac.compare_digest(supplied.encode(), secret.encode())
    ):
        raise WebhookError(401, "Invalid webhook authentication")
    if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise WebhookError(415, "Better Stack requires application/json")
    try:
        payload = _object(
            json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        )
        validate_json(payload)
        return event_from_payload(payload)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WebhookError(400, "Invalid Better Stack webhook body") from exc


def event_from_payload(payload: dict[str, object]) -> BetterStackEvent | None:
    data = _object(payload.get("data"))
    family = data.get("type")
    # The documented on-call envelope has no data.type; it is not an incident.
    if family is None and "calendar_id" in data and "message" in data:
        return None
    if not isinstance(family, str) or not NAME.fullmatch(family):
        raise ValueError("Invalid Better Stack resource type")
    if family not in {"incident", "error"}:
        return None
    resource_id = identifier(data.get("id"))
    attrs = _object(data.get("attributes"))
    native = payload.get("event") if family == "incident" else attrs.get("event")
    if family == "incident" and "event" not in payload:
        native = "unspecified"
    if not isinstance(native, str) or not NAME.fullmatch(native):
        raise ValueError("Invalid Better Stack event type")

    monitor = None
    comment_id = None
    if family == "incident":
        if "relationships" in data:
            relationships = _object(data["relationships"])
            if "monitor" in relationships:
                relation = _object(relationships["monitor"])
                if "data" not in relation:
                    raise ValueError("Missing monitor relationship data")
                if relation["data"] is not None:
                    monitor_data = _object(relation["data"])
                    if monitor_data.get("type") != "monitor":
                        raise ValueError("Invalid monitor relationship type")
                    monitor = identifier(monitor_data.get("id"))
        if "comment" in payload:
            comment = _object(payload["comment"])
            if comment.get("type") != "incident_comment":
                raise ValueError("Invalid incident comment type")
            comment_id = identifier(comment.get("id"))
            _object(comment.get("attributes"))
        if native == "comment" and comment_id is None:
            raise ValueError("Missing incident comment")

    # No documented upstream delivery ID exists. Include ALL payload fields;
    # excluding mutable fields without evidence can lose legitimate transitions.
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    delivery_id = "betterstack:payload:v1:" + hashlib.sha256(canonical).hexdigest()
    return BetterStackEvent(
        provider="betterstack",
        action=family,
        delivery_id=delivery_id,
        payload=payload,
        event_type=native,
        resource_id=resource_id,
        # Team/application fields are documented on errors, not default incidents.
        team_id=_optional_id(attrs.get("team_id")) if family == "error" else None,
        application_id=_optional_id(attrs.get("application_id")) if family == "error" else None,
        monitor_id=monitor,
        environment=_optional_string(attrs.get("environment")) if family == "error" else None,
        source_url=_optional_string(attrs.get("error_url")) if family == "error" else None,
        started_at=_optional_string(attrs.get("started_at")) if family == "incident" else None,
        acknowledged_at=_optional_string(attrs.get("acknowledged_at"))
        if family == "incident"
        else None,
        resolved_at=_optional_string(attrs.get("resolved_at")) if family == "incident" else None,
        first_seen=_optional_string(attrs.get("first_seen")) if family == "error" else None,
        last_seen=_optional_string(attrs.get("last_seen")) if family == "error" else None,
        comment_id=comment_id,
    )
