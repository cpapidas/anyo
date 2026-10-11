"""Local validation and matching of Slack hook selectors."""

from __future__ import annotations

import re
from collections.abc import Mapping

from anyo.integrations.slack._events import SlackEvent

_FAMILIES = {
    "event": {
        "event_type",
        "subtype",
        "channel_type",
        "reaction",
        "include_bots",
        "ignore_self",
        "ignore_user_id",
        "callback_id",
    },
    "interaction": {
        "interaction_type",
        "callback_id",
        "action_id",
        "block_id",
        "options_request",
        "allow_legacy_app_id",
    },
    "slash_command": {"command"},
    "control": {"event_type"},
}
_COMMON = {"app_id", "team_id", "enterprise_id", "channel_id", "user_id"}
_BOOLS = {"include_bots", "ignore_self", "options_request", "allow_legacy_app_id"}
_TYPES = {"event_type", "interaction_type", "subtype"}
_LEGACY = {"interactive_message", "dialog_submission", "dialog_cancellation", "dialog_suggestion"}
_MESSAGES = {
    "message.app_home": "app_home",
    "message.channels": "channel",
    "message.groups": "group",
    "message.im": "im",
    "message.mpim": "mpim",
}


def validate(action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
    if action not in _FAMILIES:
        raise ValueError("Slack action must be event, interaction, slash_command, or control")
    unknown = configuration.keys() - (_COMMON | _FAMILIES[action])
    if unknown:
        raise ValueError("Unknown Slack configuration keys: " + ", ".join(sorted(unknown)))
    result: dict[str, str] = {}
    for key, value in configuration.items():
        if key in _BOOLS:
            if type(value) is not bool:
                raise ValueError(f"Slack {key} must be a boolean")
            result[key] = "true" if value else "false"
            continue
        if not isinstance(value, str) or len(value) > 255 or value != value.strip():
            raise ValueError(f"Slack {key} must be a string without surrounding whitespace")
        if (not value and key != "subtype") or any(ord(c) < 32 for c in value):
            raise ValueError(f"Invalid Slack {key}")
        if key in _COMMON or key == "ignore_user_id":
            if value != "*" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,127}", value):
                raise ValueError(f"Invalid Slack {key}")
            if value == "*" and key not in {"team_id", "enterprise_id"}:
                raise ValueError(f"Slack {key} cannot be a wildcard")
        if (
            key in _TYPES
            and value not in {"", "*"}
            and not re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", value)
        ):
            raise ValueError(f"Invalid Slack {key}")
        if key == "command" and value != "*" and not re.fullmatch(r"/[^\s/]{1,127}", value):
            raise ValueError("Slack command must start with / or equal *")
        result[key] = value
    if "app_id" not in result:
        raise ValueError("Slack app_id is required")
    if not ({"team_id", "enterprise_id"} & result.keys()):
        raise ValueError("Slack team_id or enterprise_id is required (use * explicitly for all)")
    selector = {
        "event": "event_type",
        "control": "event_type",
        "interaction": "interaction_type",
        "slash_command": "command",
    }[action]
    result.setdefault(selector, "*")
    if action == "event":
        result.setdefault("include_bots", "true")
        result.setdefault("ignore_self", "false")
        event_type = result[selector]
        if event_type in _MESSAGES:
            channel_type = _MESSAGES[event_type]
            if "channel_type" in result and result["channel_type"] != channel_type:
                raise ValueError("Message subscription conflicts with channel_type")
            result[selector] = "message"
            result["channel_type"] = channel_type
        if "channel_type" in result and result["channel_type"] not in {
            "channel",
            "group",
            "im",
            "mpim",
            "app_home",
        }:
            raise ValueError("Invalid Slack channel_type")
    if action == "interaction":
        result.setdefault("allow_legacy_app_id", "false")
        if result["allow_legacy_app_id"] == "true" and result[selector] not in _LEGACY:
            raise ValueError("allow_legacy_app_id requires an exact legacy interaction_type")
    return result


def matches(configuration: Mapping[str, str], event: SlackEvent) -> bool:
    if event.app_id is None:
        if configuration.get("allow_legacy_app_id") != "true" or event.event_type not in _LEGACY:
            return False
    elif event.app_id != configuration["app_id"]:
        return False
    # Match team/enterprise as one installation pair, never independently across
    # different authorizations. Remote actor IDs do not grant installation access.
    if not any(
        all(
            key not in configuration or (value is not None and configuration[key] in {"*", value})
            for key, value in (
                ("team_id", install.team_id),
                ("enterprise_id", install.enterprise_id),
            )
        )
        for install in event.installations
    ):
        return False
    for key, value in (
        ("event_type", event.event_type),
        ("interaction_type", event.event_type),
        ("command", event.command),
        ("subtype", event.subtype or ""),
        ("channel_id", event.channel_id),
        ("channel_type", event.channel_type),
        ("user_id", event.user_id),
        ("callback_id", event.callback_id),
        ("reaction", event.reaction),
    ):
        if key in configuration and configuration[key] not in {"*", value}:
            return False
    if "options_request" in configuration:
        if event.options_request != (configuration["options_request"] == "true"):
            return False
    if "action_id" in configuration or "block_id" in configuration:
        if not any(
            all(
                key not in configuration or configuration[key] == value
                for key, value in (
                    ("action_id", action.action_id),
                    ("block_id", action.block_id),
                )
            )
            for action in event.actions
        ):
            return False
    if configuration.get("include_bots") == "false" and (
        event.bot_id or event.subtype == "bot_message"
    ):
        return False
    if (
        configuration.get("ignore_self") == "true"
        and event.source_app_id == configuration["app_id"]
    ):
        return False
    if (
        configuration.get("ignore_user_id") is not None
        and configuration["ignore_user_id"] == event.user_id
    ):
        return False
    return True
