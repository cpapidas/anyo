"""Slack context carried alongside the complete, private callback payload."""

from __future__ import annotations

from dataclasses import dataclass

from anyo.hooks import HookEvent


@dataclass(frozen=True)
class SlackAction:
    """Identifiers of one action; values remain in the private payload."""

    action_id: str | None = None
    block_id: str | None = None


@dataclass(frozen=True)
class SlackInstallation:
    """A receiving installation, distinct from an actor's remote workspace."""

    team_id: str | None = None
    enterprise_id: str | None = None


@dataclass(frozen=True)
class SlackEvent(HookEvent):
    """A Slack delivery. Repository fields are unused empty strings.

    Optional fields stay absent when Slack does not supply them. ``event_type``
    is the native event/interaction type, or ``slash_command`` for commands.
    ``installations`` controls scope matching; ``team_id`` retains outer context.
    """

    event_type: str = ""
    subtype: str | None = None
    app_id: str | None = None
    team_id: str | None = None
    enterprise_id: str | None = None
    channel_id: str | None = None
    channel_type: str | None = None
    user_id: str | None = None
    event_ts: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    callback_id: str | None = None
    command: str | None = None
    reaction: str | None = None
    bot_id: str | None = None
    source_app_id: str | None = None
    actions: tuple[SlackAction, ...] = ()
    installations: tuple[SlackInstallation, ...] = ()
    options_request: bool = False
    retry_num: int | None = None
    retry_reason: str | None = None
