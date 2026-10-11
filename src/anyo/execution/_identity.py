"""Resolve durable registrations without serializing executable objects."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Sequence

from anyo.hooks import Hook


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_key(hook: Hook) -> str:
    if hook.stable_key is not None:
        return hook.stable_key
    callback = hook.pipeline
    if not inspect.isfunction(callback) or "<" in callback.__qualname__ or callback.__closure__:
        raise ValueError(
            "Durable callbacks need hook_id= for closures, lambdas, and bound callables"
        )
    return "auto-" + _digest(
        [
            hook.provider,
            hook.action,
            dict(hook.configuration),
            callback.__module__,
            callback.__qualname__,
        ]
    )


def fingerprint(hook: Hook) -> str:
    return _digest(
        [
            resolve_key(hook),
            hook.provider,
            hook.action,
            dict(hook.configuration),
            hook.version,
            hook.retry.max_attempts,
            float(hook.retry.delay),
            hook.retry.retry_interrupted,
        ]
    )


def registry(hooks: Sequence[Hook]) -> dict[str, str]:
    result: dict[str, str] = {}
    for hook in hooks:
        if hook.pipeline is None:
            continue
        key = resolve_key(hook)
        if key in result:
            raise ValueError("Durable hook identity collision; set distinct hook_id= values")
        result[key] = fingerprint(hook)
    return result
