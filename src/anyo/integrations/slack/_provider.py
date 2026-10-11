"""Slack's adapter for the shared hook/response extension contracts."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping

from anyo.hooks import EventCodec, HookEvent, WebhookDelivery
from anyo.integrations.slack._codec import SlackEventCodec
from anyo.integrations.slack._configuration import matches as matches_configuration
from anyo.integrations.slack._configuration import validate as validate_configuration
from anyo.integrations.slack._events import SlackEvent
from anyo.integrations.slack._webhooks import decode as decode_webhook


class SlackProvider:
    """Receive signed Slack HTTP callbacks without an SDK or network client.

    ``clock`` supplies Unix seconds for signature freshness validation. The
    total ``request_timeout`` includes body reading and immediate responders;
    its default leaves half a second of Slack's budget for transport overhead.
    """

    name = "slack"
    secret_env = "ANYO_SLACK_SIGNING_SECRET"
    event_codec: EventCodec = SlackEventCodec()

    def __init__(
        self, *, clock: Callable[[], float] = time.time, request_timeout: float = 2.5
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (float, int))
            or not math.isfinite(request_timeout)
            or not 0 < request_timeout < 3
        ):
            raise ValueError("Slack request_timeout must be positive and less than three seconds")
        self._clock = clock
        self.request_timeout = request_timeout

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        """Normalize selectors locally; an exact event allowlist is not required."""
        return validate_configuration(action, configuration)

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> WebhookDelivery:
        """Authenticate raw bytes, then decode event, form, or setup data."""
        return decode_webhook(body, headers, secret, now=self._clock())

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        """Apply case-sensitive selectors only to authenticated Slack events."""
        return (
            isinstance(event, SlackEvent)
            and event.provider == self.name
            and matches_configuration(configuration, event)
        )
