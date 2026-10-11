"""Versioned Slack event storage without executable serialization."""

from collections.abc import Mapping
from dataclasses import asdict, fields

from anyo.hooks import HookEvent
from anyo.integrations.slack._events import SlackAction, SlackEvent, SlackInstallation


class SlackEventCodec:
    name = "anyo.slack.v1"
    event_type: type[HookEvent] = SlackEvent

    def encode(self, event: HookEvent) -> Mapping[str, object]:
        if not isinstance(event, SlackEvent):
            raise TypeError("Slack codec requires SlackEvent")
        data = asdict(event)
        data["actions"] = [asdict(value) for value in event.actions]
        data["installations"] = [asdict(value) for value in event.installations]
        return data

    def decode(self, data: Mapping[str, object]) -> SlackEvent:
        if set(data) != {item.name for item in fields(SlackEvent)}:
            raise ValueError("Unexpected SlackEvent fields")
        values = dict(data)
        required = {"provider", "action", "delivery_id", "organization", "repository", "event_type"}
        integers = {"pull_request_number", "retry_num"}
        for name, value in values.items():
            if name in {"actions", "installations", "payload"}:
                continue
            if name == "options_request":
                valid = type(value) is bool
            elif name in integers:
                valid = value is None or type(value) is int
            else:
                valid = isinstance(value, str) or (name not in required and value is None)
            if not valid:
                raise ValueError(f"Invalid Slack event field {name}")
        if values["provider"] != "slack" or not isinstance(values["payload"], dict):
            raise ValueError("Invalid Slack event identity or payload")
        for name, names in (
            ("actions", {"action_id", "block_id"}),
            ("installations", {"team_id", "enterprise_id"}),
        ):
            items = values[name]
            if not isinstance(items, list) or any(
                not isinstance(item, dict)
                or set(item) != names
                or any(v is not None and not isinstance(v, str) for v in item.values())
                for item in items
            ):
                raise ValueError(f"Invalid Slack {name}")
            if name == "actions":
                values[name] = tuple(SlackAction(**item) for item in items)
            else:
                values[name] = tuple(SlackInstallation(**item) for item in items)
        return SlackEvent(**values)  # type: ignore[arg-type]
