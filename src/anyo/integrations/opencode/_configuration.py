"""Validate code-based configuration without importing the HTTP dependency."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from anyo.agents import Harness, Model, PromptBackend

VERSION = "1.18.34"


@dataclass(frozen=True)
class Settings:
    models: tuple[Model, ...]
    plugins: tuple[str, ...]
    executable: str = "opencode"
    startup_timeout: float = 30
    control_timeout: float = 5
    shutdown_timeout: float = 10
    poll_interval: float = 0.5
    max_concurrency: int = 4
    state_directory: Path | None = None
    permissions: tuple[tuple[str, Literal["allow", "deny", "ask"]], ...] = (
        ("*", "deny"),
        ("read", "allow"),
        ("glob", "allow"),
        ("grep", "allow"),
        ("StructuredOutput", "allow"),
    )
    server_url: str | None = None
    server_password: str | None = field(default=None, repr=False)
    server_username: str = "opencode"

    def backend(self) -> PromptBackend:
        try:
            from anyo.integrations.opencode._runtime import OpenCodeBackend
        except ModuleNotFoundError as error:
            if error.name != "httpx2":
                raise
            raise ImportError("Install anyo[opencode] to use the OpenCode harness") from error
        return OpenCodeBackend(self)


def _url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected an HTTP(S) URL without embedded credentials, query, or fragment")
    return value.rstrip("/")


def opencode_harness(
    *,
    models: Iterable[Mapping[str, str]],
    plugins: Iterable[str] = (),
    **configuration: object,
) -> Harness:
    """Create an inert harness for use with ``PromptClient`` or ``Application``.

    Each model accepts ``model``, ``key``, and optional ``base_url``. The first
    is the default. Options: executable, state_directory, max_concurrency,
    startup_timeout, control_timeout, shutdown_timeout, poll_interval,
    permissions, and borrowed server_url/server_username/server_password.
    Keys for models under one provider must agree. Local plugins use file: URLs.
    """
    entries: list[Model] = []
    seen: set[str] = set()
    providers: dict[str, tuple[str, str | None]] = {}
    for entry in models:
        if not isinstance(entry, Mapping) or set(entry) - {"model", "key", "base_url"}:
            raise ValueError("Each model requires model/key and optionally base_url")
        name, key = entry.get("model"), entry.get("key")
        if (
            not isinstance(name, str)
            or "/" not in name
            or any(c.isspace() for c in name)
            or not all(name.split("/", 1))
            or not isinstance(key, str)
            or not key.strip()
        ):
            raise ValueError("Model requires a provider/model identifier and a nonempty API key")
        if name in seen:
            raise ValueError("Duplicate model identifier")
        base_url = entry.get("base_url")
        if base_url is not None:
            base_url = _url(base_url)
        provider = name.split("/", 1)[0]
        auth = (key, base_url)
        if provider in providers and providers[provider] != auth:
            raise ValueError("Models of one provider must use the same key and base_url")
        providers[provider] = auth
        seen.add(name)
        entries.append(Model(name, key, base_url))
    if not entries:
        raise ValueError("At least one model is required")
    if isinstance(plugins, str):
        raise TypeError("plugins must be an iterable of specifications, not one string")
    specs = tuple(plugins)
    if any(not isinstance(p, str) or not p.strip() or any(c.isspace() for c in p) for p in specs):
        raise ValueError("Plugins must be nonempty package specifications or file: URLs")
    if len(set(specs)) != len(specs):
        raise ValueError("Duplicate plugin specification")
    options = dict(configuration)
    valid = set(Settings.__dataclass_fields__) - {"models", "plugins"}
    if set(options) - valid:
        raise TypeError("Unknown OpenCode configuration option")

    def number(name: str, default: float) -> float:
        value = options.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")
        return float(value)

    def string(name: str, default: str) -> str:
        value = options.get(name, default)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a nonempty string")
        return value

    limit = options.get("max_concurrency", 4)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("max_concurrency must be a positive integer")
    state = options.get("state_directory")
    if state is not None and not isinstance(state, (str, Path)):
        raise TypeError("state_directory must be a path")
    server_url = _url(string("server_url", "")) if "server_url" in options else None
    password = string("server_password", "") if "server_password" in options else None
    if server_url and password is None:
        raise ValueError("Borrowed servers require server_password")
    rules: list[tuple[str, Literal["allow", "deny", "ask"]]] = []
    if "permissions" in options:
        supplied = options["permissions"]
        if not isinstance(supplied, Mapping) or "*" not in supplied:
            raise ValueError("permissions must be a mapping including '*'")
        for tool, action in supplied.items():
            if not isinstance(tool, str) or not tool:
                raise ValueError("Permission names must be nonempty strings")
            if action == "allow":
                rules.append((tool, "allow"))
            elif action == "deny":
                rules.append((tool, "deny"))
            elif action == "ask":
                rules.append((tool, "ask"))
            else:
                raise ValueError("Permission actions must be allow/deny/ask")
    else:
        rules = [("*", "deny"), ("read", "allow"), ("glob", "allow"), ("grep", "allow")]
    # This internal tool returns the envelope; denying it prevents every result.
    if any(name == "StructuredOutput" and action != "allow" for name, action in rules):
        raise ValueError("StructuredOutput is reserved and must be allowed")
    rules = [(name, action) for name, action in rules if name != "StructuredOutput"]
    rules.append(("StructuredOutput", "allow"))
    settings = Settings(
        tuple(entries),
        specs,
        executable=string("executable", "opencode"),
        startup_timeout=number("startup_timeout", 30),
        control_timeout=number("control_timeout", 5),
        shutdown_timeout=number("shutdown_timeout", 10),
        poll_interval=number("poll_interval", 0.5),
        max_concurrency=limit,
        state_directory=Path(state).expanduser().resolve() if state is not None else None,
        permissions=tuple(rules),
        server_url=server_url,
        server_password=password,
        server_username=string("server_username", "opencode"),
    )
    return Harness("opencode", settings.models, settings.plugins, settings.backend)
