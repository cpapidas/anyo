"""Receive Notion changes with the existing queue and worker runtime."""

import os
from typing import Literal, cast

import anyo
from anyo import HookEvent
from anyo.integrations.notion import NotionEvent


def page_changed(event: HookEvent) -> None:
    """Replace this summary with your workflow; fetching content is optional."""
    if isinstance(event, NotionEvent):
        print(f"Notion {event.event_type}: {event.entity_id}", flush=True)


def main() -> None:
    anyo.add_hook(
        provider="notion",
        action="event",
        event_type=os.environ.get("ANYO_NOTION_EVENT_TYPE", "page.content_updated"),
        workspace_id=os.environ["ANYO_NOTION_WORKSPACE_ID"],
        subscription_id=os.environ["ANYO_NOTION_SUBSCRIPTION_ID"],
        pipeline=page_changed,
        hook_id="notion-page-changed",
    )
    role = os.environ.get("ANYO_ROLE", "all")
    mode = os.environ.get("ANYO_EXECUTION_MODE", "thread")
    if role not in {"all", "receiver", "worker"} or mode not in {"thread", "process"}:
        raise ValueError("Invalid ANYO_ROLE or ANYO_EXECUTION_MODE")
    anyo.start(
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/notion.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "notion"),
        role=cast(Literal["all", "receiver", "worker"], role),
        execution_mode=cast(Literal["thread", "process"], mode),
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYO_PORT", "8000")),
        retention_seconds=172800,
    )


if __name__ == "__main__":
    main()
