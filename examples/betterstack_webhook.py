"""Start a local pipeline from a Better Stack incident or new error."""

import os
from typing import Literal, cast

import anyo
from anyo import HookEvent
from anyo.integrations.betterstack import BetterStackEvent


def investigate(event: HookEvent) -> None:
    """Replace this local summary with investigation or an Anyo prompt call."""
    if isinstance(event, BetterStackEvent):
        print(
            f"{event.action} {event.resource_id}: {event.event_type}; delivery={event.delivery_id}",
            flush=True,
        )


def main() -> None:
    # Select start/reopen in Better Stack. Some default incident templates have
    # no event name, so this hook accepts all notifications from that integration.
    anyo.add_hook(
        provider="betterstack",
        action="incident",
        event_type="*",
        pipeline=investigate,
        hook_id="betterstack-incidents",
    )
    anyo.add_hook(
        provider="betterstack",
        action="error",
        event_type="new_error",
        application_id=os.environ["ANYO_BETTERSTACK_APPLICATION_ID"],
        pipeline=investigate,
        hook_id="betterstack-new-errors",
    )
    role = os.environ.get("ANYO_ROLE", "all")
    mode = os.environ.get("ANYO_EXECUTION_MODE", "thread")
    if role not in {"all", "receiver", "worker"} or mode not in {"thread", "process"}:
        raise ValueError("Invalid ANYO_ROLE or ANYO_EXECUTION_MODE")
    anyo.start(
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/betterstack.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "betterstack"),
        role=cast(Literal["all", "receiver", "worker"], role),
        execution_mode=cast(Literal["thread", "process"], mode),
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYO_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
