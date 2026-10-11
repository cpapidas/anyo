"""Validate Slack acknowledgment data without importing an HTTP framework."""

from __future__ import annotations

import json
from collections.abc import Mapping

from anyo.hooks import HookResponse, WebhookResponse
from anyo.integrations.slack._events import SlackEvent


def render(event: SlackEvent, result: HookResponse) -> WebhookResponse:
    body = result.body
    dispatch = result.dispatch
    if body is not None and not isinstance(body, Mapping):
        raise ValueError("Slack responder body must be a mapping or None")
    data = dict(body) if body is not None else {}
    if event.options_request:
        _options(data, legacy=event.event_type != "block_suggestion")
    elif event.event_type == "view_submission":
        if data:
            action = data.get("response_action")
            if action == "errors":
                errors = data.get("errors")
                if (
                    not isinstance(errors, dict)
                    or not errors
                    or not all(
                        isinstance(k, str) and bool(k) and isinstance(v, str) and bool(v)
                        for k, v in errors.items()
                    )
                ):
                    raise ValueError("Modal errors must map block IDs to messages")
                dispatch = False
            elif action in {"push", "update"}:
                view = data.get("view")
                if (
                    not isinstance(view, dict)
                    or view.get("type") != "modal"
                    or not isinstance(view.get("blocks"), list)
                ):
                    raise ValueError("Modal response requires a modal view with blocks")
            elif action != "clear":
                raise ValueError("Invalid modal response_action")
    elif event.event_type == "dialog_submission":
        if data:
            errors = data.get("errors")
            if (
                set(data) != {"errors"}
                or not isinstance(errors, list)
                or not errors
                or not all(
                    isinstance(e, dict)
                    and isinstance(e.get("name"), str)
                    and isinstance(e.get("error"), str)
                    for e in errors
                )
            ):
                raise ValueError("Dialog response requires named errors")
            dispatch = False
    elif event.action == "slash_command" or event.event_type == "interactive_message":
        if data:
            if not (
                {
                    "text",
                    "blocks",
                    "attachments",
                    "response_type",
                    "replace_original",
                    "delete_original",
                }
                & data.keys()
            ):
                raise ValueError("Invalid Slack message response")
            for key, kind in (
                ("text", str),
                ("blocks", list),
                ("attachments", list),
                ("replace_original", bool),
                ("delete_original", bool),
            ):
                if key in data and not isinstance(data[key], kind):
                    raise ValueError("Invalid Slack message response field")
            if "response_type" in data and data["response_type"] not in ("ephemeral", "in_channel"):
                raise ValueError("Invalid Slack response_type")
    elif data:
        # Events and acknowledgments do not implicitly publish messages. Users
        # can call a response URL or API from their explicitly supplied handler.
        raise ValueError("This Slack callback requires an empty acknowledgment")
    encoded = (
        json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if data
        else b""
    )
    if len(encoded) > 1048576:
        raise ValueError("Slack response exceeds one MiB")
    return WebhookResponse(body=encoded, dispatch=dispatch)


def _text(value: object, *, legacy: bool) -> bool:
    if legacy:
        return isinstance(value, str) and bool(value)
    return (
        isinstance(value, dict)
        and value.get("type") == "plain_text"
        and isinstance(value.get("text"), str)
        and bool(value["text"])
    )


def _options(data: dict[str, object], *, legacy: bool) -> None:
    if set(data) not in ({"options"}, {"option_groups"}):
        raise ValueError("Option response requires options or option_groups")
    items = data.get("options", data.get("option_groups"))
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Option response requires a list of at most 100 entries")
    groups = items if "option_groups" in data else [{"options": items}]
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Invalid option group")
        if "option_groups" in data and not _text(group.get("label"), legacy=legacy):
            # Legacy groups call this field text, not label.
            if not legacy or not _text(group.get("text"), legacy=True):
                raise ValueError("Invalid option group label")
        options = group.get("options")
        if not isinstance(options, list) or len(options) > 100:
            raise ValueError("Invalid options list")
        for option in options:
            if (
                not isinstance(option, dict)
                or not _text(option.get("text"), legacy=legacy)
                or not isinstance(option.get("value"), str)
            ):
                raise ValueError("Options require text and a string value")
