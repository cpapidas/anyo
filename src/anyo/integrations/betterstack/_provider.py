"""Better Stack's implementation of the shared provider and codec contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping

from anyo.hooks import EventCodec, HookEvent, WebhookDelivery, WebhookResponse
from anyo.integrations.betterstack._codec import BetterStackEventCodec
from anyo.integrations.betterstack._events import BetterStackEvent
from anyo.integrations.betterstack._webhooks import NAME, decode, identifier


class BetterStackProvider:
    """Receive JSON incident/error notifications authenticated by a secret header.

    Set ``X-Anyo-Webhook-Secret`` in Better Stack to the hook secret (printable
    ASCII without whitespace or commas). Use HTTPS. No HMAC signature, freshness
    guarantee, Basic-auth fallback, or remote configuration is implied.
    """

    name = "betterstack"
    secret_env = "ANYO_BETTERSTACK_WEBHOOK_SECRET"
    event_codec: EventCodec = BetterStackEventCodec()

    def __init__(self, *, request_timeout: float = 5) -> None:
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout)
            or not 0 < request_timeout < 25
        ):
            raise ValueError("Better Stack request_timeout must be positive and less than 25")
        self.request_timeout = request_timeout

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        """Validate explicit event selection and optional exact context filters."""
        if action not in {"incident", "error"}:
            raise ValueError("Better Stack action must be incident or error")
        allowed = {"event_type", "resource_id"} | (
            {"monitor_id"} if action == "incident" else {"team_id", "application_id", "environment"}
        )
        if configuration.keys() - allowed:
            raise ValueError("Unknown Better Stack configuration keys")
        native = configuration.get("event_type")
        if not isinstance(native, str) or (native != "*" and not NAME.fullmatch(native)):
            raise ValueError("Better Stack event_type is required; use * explicitly for all")
        result = {"event_type": native}
        for key, value in configuration.items():
            if key == "event_type":
                continue
            if key == "environment":
                if (
                    not isinstance(value, str)
                    or not value
                    or value != value.strip()
                    or len(value) > 255
                    or any(ord(c) < 32 for c in value)
                ):
                    raise ValueError("Invalid Better Stack environment")
                result[key] = value
            else:
                result[key] = identifier(value)
        return result

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> WebhookDelivery:
        """Authenticate before interpreting JSON; return an empty admission reply."""
        return WebhookDelivery(
            event=decode(body, headers, secret),
            response=WebhookResponse(media_type="text/plain"),
        )

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        """Missing context never satisfies an exact filter."""
        if not isinstance(event, BetterStackEvent) or event.provider != self.name:
            return False
        return all(
            (key == "event_type" and value == "*") or getattr(event, key, None) == value
            for key, value in configuration.items()
        )
