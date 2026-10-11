"""Versioned Better Stack event transport; no type imports from stored data."""

from collections.abc import Mapping
from dataclasses import asdict, fields

from anyo.hooks import HookEvent
from anyo.integrations.betterstack._events import BetterStackEvent
from anyo.integrations.betterstack._webhooks import event_from_payload, validate_json


class BetterStackEventCodec:
    name = "anyo.betterstack.v1"
    event_type: type[HookEvent] = BetterStackEvent

    def encode(self, event: HookEvent) -> Mapping[str, object]:
        if type(event) is not BetterStackEvent:
            raise ValueError("Better Stack codec requires BetterStackEvent")
        data = asdict(event)
        self.decode(data)
        return data

    def decode(self, data: Mapping[str, object]) -> BetterStackEvent:
        if set(data) != {item.name for item in fields(BetterStackEvent)}:
            raise ValueError("Unexpected Better Stack event fields")
        validate_json(dict(data))
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Invalid Better Stack event payload")
        event = event_from_payload(payload)
        # Reconstruct only our trusted class and verify all stored derived fields.
        if event is None or asdict(event) != dict(data):
            raise ValueError("Inconsistent Better Stack event context")
        return event
