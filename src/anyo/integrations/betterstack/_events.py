"""Native Better Stack context carried through Anyo workers."""

from dataclasses import dataclass, field

from anyo.hooks import HookEvent


@dataclass(frozen=True)
class BetterStackEvent(HookEvent):
    """One authenticated notification, not an individual exception occurrence.

    ``event_type`` preserves the native name, or is ``unspecified`` for an
    incident template without an event discriminator. ``delivery_id`` is a
    versioned payload fingerprint, not an upstream notification ID. Source
    timestamps are retained as supplied and do not establish delivery order.
    """

    event_type: str = ""
    resource_id: str = ""
    team_id: str | None = None
    application_id: str | None = None
    monitor_id: str | None = None
    environment: str | None = field(default=None, repr=False)
    source_url: str | None = field(default=None, repr=False)
    started_at: str | None = None
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    comment_id: str | None = None
