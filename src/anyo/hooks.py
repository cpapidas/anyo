"""Provider-independent hook definitions and extension contracts."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "Hook",
    "HookEvent",
    "HookResponse",
    "Pipeline",
    "Provider",
    "Responder",
    "ResponseProvider",
    "ResponderCapability",
    "RetryPolicy",
    "EventCodec",
    "WebhookDelivery",
    "WebhookError",
    "WebhookResponse",
]


@dataclass(frozen=True)
class HookEvent:
    """One verified provider event. Each callback receives a private payload copy."""

    provider: str
    action: str
    delivery_id: str
    organization: str = ""
    repository: str = ""
    payload: Mapping[str, object] = field(default_factory=dict, repr=False)
    pull_request_number: int | None = None
    head_sha: str | None = None


Pipeline = Callable[[HookEvent], object]


class EventCodec(Protocol):
    """Explicit, trusted serialization for one exact event class.

    Register codecs in both receiver and worker. Names include a schema version;
    decoding never imports a type named by stored data. Values must be finite JSON.
    """

    name: str
    event_type: type[HookEvent]

    def encode(self, event: HookEvent) -> Mapping[str, object]: ...
    def decode(self, data: Mapping[str, object]) -> HookEvent: ...


@runtime_checkable
class EventCodecProvider(Protocol):
    """Optional provider-owned codec for durable event transport."""

    event_codec: EventCodec


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded replay policy; interrupted attempts require a separate opt-in.

    max_attempts includes the first execution. Delay doubles after each failure,
    capped at one hour. External effects must be idempotent before enabling replay.
    """

    max_attempts: int = 1
    delay: float = 1
    retry_interrupted: bool = False

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be an integer from 1 to 100")
        if (
            isinstance(self.delay, bool)
            or not isinstance(self.delay, (int, float))
            or not math.isfinite(self.delay)
            or not 0 <= self.delay <= 3600
        ):
            raise ValueError("retry delay must be finite and between 0 and 3600")
        if type(self.retry_interrupted) is not bool:
            raise ValueError("retry_interrupted must be a boolean")


@dataclass(frozen=True)
class HookResponse:
    """Immediate responder result, interpreted by its provider.

    ``dispatch=False`` suppresses every background pipeline for this request.
    Payloads can contain private data and are never included in representations.
    """

    body: Mapping[str, object] | None = field(default=None, repr=False)
    dispatch: bool = True


Responder = Callable[[HookEvent], Awaitable[HookResponse]]


@dataclass(frozen=True)
class WebhookResponse:
    """Transport-neutral, provider-validated reply and admission decision."""

    body: bytes = field(default=b"", repr=False)
    status_code: int = 200
    media_type: str = "application/json"
    dispatch: bool = True


@dataclass(frozen=True)
class WebhookDelivery:
    """A verified request with a provider-owned response policy.

    Setup exchanges have no event. ``render`` validates responder results;
    ``requires_response`` rejects requests without a matching responder.
    """

    event: HookEvent | None = None
    response: WebhookResponse = field(default_factory=WebhookResponse)
    render: Callable[[HookResponse], WebhookResponse] | None = field(default=None, repr=False)
    requires_response: bool = False


@dataclass(frozen=True)
class Hook:
    """Registered callback and normalized provider configuration."""

    id: str
    provider: str
    action: str
    configuration: Mapping[str, str]
    pipeline: Pipeline | None = field(repr=False)
    secret: str | None = field(default=None, repr=False)
    responder: Responder | None = field(default=None, repr=False)
    stable_key: str | None = None
    version: str = "1"
    retry: RetryPolicy = field(default_factory=RetryPolicy)


class WebhookError(ValueError):
    """A rejected provider delivery, with a safe client-facing message."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class Provider(Protocol):
    """An adapter validates configuration and authenticates/decodes deliveries.

    Headers passed to ``decode`` have lowercase names and repeated values joined
    with comma-space. Reject ambiguous credentials. Return ``None`` for
    authenticated events that need no pipeline. Never trust an event before
    authenticating the request. Configuration mappings must contain strings.

    Implementations live in ``anyo.integrations.<provider>`` packages. Adapters
    need not inherit this protocol; supplying a matching object to Application
    is sufficient. The application owns HTTP reception and execution lifecycle.
    """

    name: str
    secret_env: str

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        """Validate an action and provider options; return a normalized snapshot.

        Raise ValueError for unsupported actions or invalid configuration.
        Registration is local: this method must not make provider API calls.
        """
        ...

    def decode(
        self, body: bytes, headers: Mapping[str, str], secret: str
    ) -> HookEvent | WebhookDelivery | None:
        """Authenticate and translate a delivery, or acknowledge it with None.

        Raise WebhookError(401, ...) for failed authentication and another safe
        4xx WebhookError for invalid authenticated input. The event's provider
        must equal name. Response-aware adapters may return WebhookDelivery.
        Do not perform network I/O or execute user pipelines.
        """
        ...

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        """Match normalized hook options against an already verified event."""
        ...


@runtime_checkable
class ResponseProvider(Provider, Protocol):
    """Optional extension for providers that support immediate responders.

    ``request_timeout`` bounds the entire receive/response operation. Old
    providers continue returning HookEvent/None and need no new members.
    Providers that only need a deadline/acknowledgment may implement
    ``ResponderCapability`` with ``supports_responders=False`` to reject
    consumer responders during registration.
    """

    request_timeout: float

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> WebhookDelivery: ...


@runtime_checkable
class ResponderCapability(Protocol):
    """Optional response-provider opt-out; absence preserves responder support."""

    supports_responders: bool
