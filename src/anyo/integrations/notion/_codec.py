"""Trusted, versioned transport of Notion events across queue/worker boundaries."""

from collections.abc import Mapping
from dataclasses import asdict, fields

from anyo.hooks import HookEvent
from anyo.integrations.notion._events import NotionEvent
from anyo.integrations.notion._webhooks import event_from_payload, validate_json


class NotionEventCodec:
    name = "anyo.notion.v1"
    event_type: type[HookEvent] = NotionEvent

    def encode(self, event: HookEvent) -> Mapping[str, object]:
        if type(event) is not NotionEvent:
            raise ValueError("Notion codec requires NotionEvent")
        data = asdict(event)
        self.decode(data)
        return data

    def decode(self, data: Mapping[str, object]) -> NotionEvent:
        if set(data) != {item.name for item in fields(NotionEvent)}:
            raise ValueError("Unexpected Notion event fields")
        validate_json(dict(data))
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Invalid Notion event payload")
        event = event_from_payload(payload)
        if asdict(event) != dict(data) or type(data["attempt_number"]) is not int:
            raise ValueError("Inconsistent Notion event context")
        return event
