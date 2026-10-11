"""Explicit event codecs and a bounded, versioned JSON envelope."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import fields

from anyo.hooks import EventCodec, HookEvent

MAX_EVENT_BYTES = 1048576


def validate_json(value: object, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("Event nesting exceeds 64 levels")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            validate_json(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            validate_json(item, depth + 1)
        return
    raise ValueError("Event data must contain finite JSON values")


def validate_base(data: Mapping[str, object]) -> None:
    for name in ("provider", "action", "delivery_id", "organization", "repository"):
        if not isinstance(data.get(name), str):
            raise ValueError(f"Invalid event {name}")
    for name in ("provider", "action", "delivery_id"):
        if not data[name]:
            raise ValueError(f"Empty event {name}")
    if not isinstance(data.get("payload"), dict):
        raise ValueError("Event payload must be a JSON object")
    number = data.get("pull_request_number")
    if number is not None and type(number) is not int:
        raise ValueError("Invalid pull_request_number")
    sha = data.get("head_sha")
    if sha is not None and not isinstance(sha, str):
        raise ValueError("Invalid head_sha")


class HookEventCodec:
    name = "anyo.hook.v1"
    event_type = HookEvent

    def encode(self, event: HookEvent) -> Mapping[str, object]:
        data = {field.name: getattr(event, field.name) for field in fields(HookEvent)}
        data["payload"] = dict(event.payload)
        validate_base(data)
        return data

    def decode(self, data: Mapping[str, object]) -> HookEvent:
        if set(data) != {field.name for field in fields(HookEvent)}:
            raise ValueError("Unexpected HookEvent fields")
        validate_base(data)
        # The explicit field validation above is the runtime boundary; dataclass
        # construction is driven only by a trusted, fixed field set.
        return HookEvent(**data)  # type: ignore[arg-type]


class EventCodecRegistry:
    def __init__(self, codecs: Iterable[EventCodec] = ()) -> None:
        self._names: dict[str, EventCodec] = {}
        self._types: dict[type[HookEvent], EventCodec] = {}
        for codec in (HookEventCodec(), *codecs):
            if not isinstance(codec.name, str) or not codec.name:
                raise ValueError("Event codec needs a nonempty versioned name")
            if codec.name in self._names or codec.event_type in self._types:
                raise ValueError("Duplicate event codec name or event class")
            if not isinstance(codec.event_type, type) or not issubclass(
                codec.event_type, HookEvent
            ):
                raise TypeError("Event codecs must declare a HookEvent subclass")
            self._names[codec.name] = codec
            self._types[codec.event_type] = codec

    def encode(self, event: HookEvent) -> str:
        codec = self._types.get(type(event))
        if codec is None:
            raise ValueError("No registered codec for this event class")
        data = dict(codec.encode(event))
        validate_json(data)
        envelope = {"schema": 1, "codec": codec.name, "data": data}
        text = json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        if len(text.encode()) > MAX_EVENT_BYTES:
            raise ValueError("Encoded event exceeds 1 MiB")
        return text

    def decode(self, text: str) -> HookEvent:
        if len(text.encode()) > MAX_EVENT_BYTES:
            raise ValueError("Encoded event exceeds 1 MiB")
        value = json.loads(text)
        validate_json(value)
        if not isinstance(value, dict) or set(value) != {"schema", "codec", "data"}:
            raise ValueError("Invalid event envelope")
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise ValueError("Unsupported event schema")
        name = value["codec"]
        data = value["data"]
        if not isinstance(name, str) or name not in self._names or not isinstance(data, dict):
            raise ValueError("Unsupported event codec")
        codec = self._names[name]
        event = codec.decode(data)
        if type(event) is not codec.event_type:
            raise ValueError("Event codec returned an unexpected class")
        return event
