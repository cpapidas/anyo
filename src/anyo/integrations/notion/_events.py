"""Native Notion context, independent of an outbound API client."""

from dataclasses import dataclass

from anyo.hooks import HookEvent


@dataclass(frozen=True)
class NotionEvent(HookEvent):
    """A change notification, not a content snapshot or an individual edit.

    ``delivery_id`` scopes the native ``event_id`` to its source subscription.
    ``attempt_number`` is the admitted HTTP delivery attempt, not a worker retry.
    Optional context remains absent when the source omits it. The inherited
    private ``payload`` retains native IDs, authors, and event-specific data.
    """

    event_id: str = ""
    event_type: str = ""
    workspace_id: str = ""
    integration_id: str = ""
    subscription_id: str = ""
    entity_id: str = ""
    entity_type: str | None = None
    timestamp: str = ""
    attempt_number: int = 1
    api_version: str | None = None
