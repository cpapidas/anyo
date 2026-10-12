"""Notion's local provider implementation; no SDK or REST credentials required."""

from __future__ import annotations

import math
from collections.abc import Mapping

from anyo.hooks import EventCodec, HookEvent, WebhookDelivery, WebhookResponse
from anyo.integrations.notion._codec import NotionEventCodec
from anyo.integrations.notion._events import NotionEvent
from anyo.integrations.notion._webhooks import decode, entity_name, event_name, identifier


class NotionProvider:
    """Receive signed connection webhooks with exact native event selection.

    ``secret=`` or ``ANYO_NOTION_VERIFICATION_TOKEN`` holds a token already
    confirmed in Notion's UI. The setup command collects an untrusted candidate
    separately. ``request_timeout`` bounds reception/admission, not pipelines;
    its five-second default is Anyo policy, not a Notion delivery guarantee.
    """

    name = "notion"
    secret_env = "ANYO_NOTION_VERIFICATION_TOKEN"
    supports_responders = False
    event_codec: EventCodec = NotionEventCodec()

    def __init__(self, *, request_timeout: float = 5) -> None:
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
        ):
            raise ValueError("Notion request_timeout must be positive and finite")
        self.request_timeout = request_timeout

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        if action != "event":
            raise ValueError("Notion action must be event")
        ids = {"workspace_id", "integration_id", "subscription_id", "entity_id"}
        if configuration.keys() - ids - {"event_type", "entity_type"}:
            raise ValueError("Unknown Notion configuration keys")
        native = configuration.get("event_type")
        result = {"event_type": "*" if native == "*" else event_name(native)}
        for key, value in configuration.items():
            if key in ids:
                result[key] = identifier(value)
            elif key == "entity_type":
                result[key] = entity_name(value)
        return result

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> WebhookDelivery:
        return WebhookDelivery(
            event=decode(body, headers, secret),
            response=WebhookResponse(media_type="text/plain"),
        )

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        if not isinstance(event, NotionEvent) or event.provider != self.name:
            return False
        return all(
            (key == "event_type" and value == "*") or getattr(event, key, None) == value
            for key, value in configuration.items()
        )
