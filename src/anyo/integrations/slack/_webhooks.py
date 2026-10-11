"""Authenticate and normalize Slack HTTP requests; no outbound I/O."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from functools import partial
from typing import NoReturn
from urllib.parse import parse_qsl

from anyo.hooks import WebhookDelivery, WebhookError, WebhookResponse
from anyo.integrations.slack._events import SlackAction, SlackEvent, SlackInstallation
from anyo.integrations.slack._responses import render

_NAME = re.compile(r"[a-z][a-z0-9_.]{0,127}")
_LEGACY = {"interactive_message", "dialog_submission", "dialog_cancellation", "dialog_suggestion"}


def decode(body: bytes, headers: Mapping[str, str], secret: str, *, now: float) -> WebhookDelivery:
    signature = headers.get("x-slack-signature", "")
    timestamp = headers.get("x-slack-request-timestamp", "")
    if not re.fullmatch(r"v0=[0-9a-f]{64}", signature) or not re.fullmatch(
        r"[0-9]{1,12}", timestamp
    ):
        raise WebhookError(401, "Missing or invalid Slack signature")
    if abs(now - int(timestamp)) > 300:
        raise WebhookError(401, "Slack request timestamp is outside the allowed window")
    expected = (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(signature, expected):
        raise WebhookError(401, "Invalid Slack signature")

    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        payload = _json(body)
        outer_type = _name(payload.get("type"))
        if outer_type == "url_verification":
            challenge = _required_string(payload.get("challenge"), "challenge")
            return WebhookDelivery(
                response=WebhookResponse(
                    body=json.dumps({"challenge": challenge}).encode(),
                    dispatch=False,
                )
            )
        if outer_type == "event_callback":
            inner = _object(payload.get("event"), "event")
            event_type = _name(inner.get("type"))
            family = "event"
            event_id = _required_string(payload.get("event_id"), "event_id")
        else:
            inner = payload
            event_type = outer_type
            family = "control"
            event_id = None
            if outer_type == "app_rate_limited":
                _required_string(payload.get("team_id"), "team_id")
                minute = payload.get("minute_rate_limited")
                if type(minute) is not int or minute < 0:
                    raise WebhookError(400, "Invalid Slack rate-limit minute")
    elif content_type == "application/x-www-form-urlencoded":
        form = _form(body)
        if "payload" in form:
            if len(form) != 1:
                raise WebhookError(400, "Ambiguous Slack interaction body")
            payload = _json(form["payload"].encode())
            event_type = _name(payload.get("type"))
            inner = payload
            family = "interaction"
            event_id = None
        elif "ssl_check" in form:
            if form["ssl_check"] != "1" or "command" in form:
                raise WebhookError(400, "Invalid Slack SSL probe")
            return WebhookDelivery(response=WebhookResponse(dispatch=False))
        else:
            payload = dict(form)
            command = _required_string(payload.get("command"), "command")
            if not re.fullmatch(r"/[^\s/]{1,127}", command):
                raise WebhookError(400, "Invalid Slack command")
            inner = payload
            family = "slash_command"
            event_type = "slash_command"
            event_id = None
    else:
        raise WebhookError(415, "Slack requires JSON or URL-encoded form content")

    app_id = _string(payload.get("api_app_id"))
    if app_id is None and event_type not in _LEGACY:
        raise WebhookError(400, "Missing Slack api_app_id")
    team = _id(payload.get("team")) or _string(payload.get("team_id"))
    enterprise = (
        _id(payload.get("enterprise"))
        or _string(payload.get("enterprise_id"))
        or _string(_optional_object(payload.get("team")).get("enterprise_id"))
    )
    if family == "slash_command":
        _required_string(payload.get("user_id"), "user_id")
        _required_string(payload.get("channel_id"), "channel_id")
    if family == "interaction":
        _required_string(_object(payload.get("user"), "user").get("id"), "user.id")
        if event_type in {"view_submission", "view_closed"}:
            _object(payload.get("view"), "view")
        if event_type in {"shortcut", "message_action", "dialog_submission", "dialog_cancellation"}:
            _required_string(payload.get("callback_id"), "callback_id")
        if event_type == "block_actions":
            action_values = payload.get("actions")
            if not isinstance(action_values, list) or not action_values:
                raise WebhookError(400, "Slack block_actions requires actions")
            for value in action_values:
                _required_string(_object(value, "action").get("action_id"), "action_id")
    installations = _installations(payload, team, enterprise)
    if not installations:
        raise WebhookError(400, "Missing Slack installation context")

    message = _optional_object(inner.get("message"))
    previous = _optional_object(inner.get("previous_message"))
    item = _optional_object(inner.get("item"))
    view = _optional_object(inner.get("view"))
    edited = _optional_object(message.get("edited"))
    function = _optional_object(inner.get("function"))
    bot_profile = _optional_object(inner.get("bot_profile")) or _optional_object(
        message.get("bot_profile")
    )
    subtype = _string(inner.get("subtype"))
    actions = _actions(inner)
    options_request = event_type in {"block_suggestion", "dialog_suggestion"} or (
        event_type == "interactive_message" and "actions" not in inner and "name" in inner
    )
    if options_request and not actions:
        raise WebhookError(400, "Slack options request needs an action identifier")

    if event_id is not None:
        identity = f"slack:{app_id}:{event_id}"
    elif family == "control" and event_type == "app_rate_limited":
        identity = f"slack:{app_id}:{team}:rate:{payload['minute_rate_limited']}"
    else:
        canonical = {k: v for k, v in payload.items() if k != "token"}
        invocation = (
            payload.get("trigger_id")
            or payload.get("action_ts")
            or any(isinstance(a, dict) and a.get("action_ts") for a in _list(inner.get("actions")))
        )
        identity = (
            "slack:"
            + hashlib.sha256(
                json.dumps(
                    [canonical, None if invocation else timestamp],
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        )

    retry = headers.get("x-slack-retry-num")
    if retry is not None and not re.fullmatch(r"[0-9]{1,6}", retry):
        raise WebhookError(400, "Invalid Slack retry number")
    event = SlackEvent(
        provider="slack",
        action=family,
        delivery_id=identity,
        payload=payload,
        event_type=event_type,
        subtype=subtype,
        app_id=app_id,
        team_id=team,
        enterprise_id=enterprise,
        installations=installations,
        channel_id=_id(inner.get("channel"))
        or _string(inner.get("channel_id"))
        or _id(item.get("channel"))
        or _id(message.get("channel")),
        channel_type=_string(inner.get("channel_type")) or _string(item.get("channel_type")),
        user_id=_id(inner.get("user"))
        or _string(inner.get("user_id"))
        or _id(edited.get("user"))
        or _id(message.get("user"))
        or _id(previous.get("user")),
        event_ts=_string(inner.get("event_ts")),
        message_ts=_string(message.get("ts"))
        or _string(inner.get("deleted_ts"))
        or _string(inner.get("message_ts"))
        or _string(inner.get("ts"))
        or _string(item.get("ts")),
        thread_ts=_string(inner.get("thread_ts"))
        or _string(message.get("thread_ts"))
        or _string(previous.get("thread_ts")),
        callback_id=_string(inner.get("callback_id"))
        or _string(view.get("callback_id"))
        or _string(function.get("callback_id")),
        command=_string(inner.get("command")),
        reaction=_string(inner.get("reaction")),
        bot_id=_string(inner.get("bot_id")) or _string(message.get("bot_id")),
        source_app_id=(
            _string(inner.get("app_id"))
            or _string(message.get("app_id"))
            or _string(bot_profile.get("app_id"))
        )
        if event_type in {"message", "app_mention"}
        else None,
        actions=actions,
        options_request=options_request,
        retry_num=int(retry) if retry is not None else None,
        retry_reason=_string(headers.get("x-slack-retry-reason")),
    )
    return WebhookDelivery(
        event=event, render=partial(render, event), requires_response=options_request
    )


def _installations(
    payload: Mapping[str, object], team: str | None, enterprise: str | None
) -> tuple[SlackInstallation, ...]:
    installs: tuple[SlackInstallation, ...] = ()
    if "authorizations" in payload:
        auth = payload["authorizations"]
        if not isinstance(auth, list):
            raise WebhookError(400, "Invalid Slack authorizations")
        installs = tuple(
            SlackInstallation(
                _string(_object(a, "authorization").get("team_id")),
                _string(_object(a, "authorization").get("enterprise_id")),
            )
            for a in auth
        )
        installs = tuple(i for i in installs if i.team_id or i.enterprise_id)
    # context_* denotes the receiving installation, even for Slack Connect.
    context_team = _string(payload.get("context_team_id"))
    context_enterprise = _string(payload.get("context_enterprise_id"))
    if context_team or context_enterprise:
        if context_team and not context_enterprise:
            enterprises = {i.enterprise_id for i in installs if i.team_id == context_team}
            if len(enterprises) == 1:
                context_enterprise = next(iter(enterprises))
        return (SlackInstallation(context_team, context_enterprise or enterprise),)
    if installs:
        return installs
    return (SlackInstallation(team, enterprise),) if team or enterprise else ()


def _actions(inner: Mapping[str, object]) -> tuple[SlackAction, ...]:
    if "actions" in inner:
        values = inner["actions"]
        if not isinstance(values, list):
            raise WebhookError(400, "Invalid Slack actions")
        result = []
        for value in values:
            action = _object(value, "action")
            result.append(
                SlackAction(
                    _string(action.get("action_id")) or _string(action.get("name")),
                    _string(action.get("block_id")),
                )
            )
        return tuple(result)
    if "action_id" in inner or "name" in inner:
        return (
            SlackAction(
                _string(inner.get("action_id")) or _string(inner.get("name")),
                _string(inner.get("block_id")),
            ),
        )
    return ()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise ValueError("Non-JSON numeric constant")


def _json(body: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(
            body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        # JSON's escaped surrogates and overflowed floats are accepted by the
        # standard decoder but cannot safely travel through payload copies or
        # response encoders. Reject them at the authenticated boundary.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise WebhookError(400, "Invalid Slack JSON body") from None
    return _object(value, "body")


def _form(body: bytes) -> dict[str, str]:
    try:
        text = body.decode("utf-8")
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError("Bad percent encoding")
        pairs = parse_qsl(
            text, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=100
        )
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate form key")
        return result
    except (ValueError, UnicodeError):
        raise WebhookError(400, "Invalid Slack form body") from None


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WebhookError(400, f"Invalid Slack {field}")
    return value


def _optional_object(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _id(value: object) -> str | None:
    return _string(value.get("id")) if isinstance(value, dict) else _string(value)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise WebhookError(400, f"Missing or invalid Slack {field}")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise WebhookError(400, "Missing or invalid Slack type")
    return value
