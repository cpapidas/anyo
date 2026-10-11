"""Validate native OmO settings without starting a process."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from anyo.agents import Harness, Model, PromptBackend

VERSION = "5.1.21"


@dataclass(frozen=True)
class Settings:
    models: tuple[Model, ...]
    executable: str = "omo"
    tools: tuple[str, ...] = ("read", "grep", "find", "ls")
    max_concurrency: int = 4
    startup_timeout: float = 30
    shutdown_timeout: float = 5
    state_directory: Path | None = None

    def backend(self) -> PromptBackend:
        from anyo.integrations.omo._runtime import OmoBackend

        return OmoBackend(self)


def omo_harness(
    *,
    models: Iterable[Mapping[str, str]],
    plugins: Iterable[str] = (),
    **configuration: object,
) -> Harness:
    """Build an inert native OmO harness for ``PromptClient`` or ``Application``.

    Models use OmO provider/model identifiers, keys, and optional base_url.
    Options: executable, tools (exact tool names), max_concurrency,
    startup_timeout, shutdown_timeout, state_directory. OpenCode plugins are
    unsupported. StructuredOutput is always enabled. Tool policy is not a sandbox.
    """
    if isinstance(plugins, str) or tuple(plugins):
        raise ValueError("Native OmO does not use OpenCode plugins; omit plugins")
    entries: list[Model] = []
    names: set[str] = set()
    credentials: dict[str, tuple[str, str | None]] = {}
    for entry in models:
        if not isinstance(entry, Mapping) or set(entry) - {"model", "key", "base_url"}:
            raise ValueError("Each model requires model/key and optionally base_url")
        name, key, url = entry.get("model"), entry.get("key"), entry.get("base_url")
        if (
            not isinstance(name, str)
            or "/" not in name
            or not all(name.split("/", 1))
            or any(c.isspace() for c in name)
            or not isinstance(key, str)
            or not key.strip()
        ):
            raise ValueError("Model requires a provider/model identifier and a nonempty API key")
        if name in names:
            raise ValueError("Duplicate model identifier")
        if url is not None:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("base_url must be HTTP(S) without credentials, query or fragment")
            url = url.rstrip("/")
        provider = name.split("/", 1)[0]
        if provider in credentials and credentials[provider] != (key, url):
            raise ValueError("Models of one provider must use the same key and base_url")
        credentials[provider] = (key, url)
        names.add(name)
        entries.append(Model(name, key, url))
    if not entries:
        raise ValueError("At least one model is required")
    if set(configuration) - (set(Settings.__dataclass_fields__) - {"models"}):
        raise TypeError("Unknown OmO configuration option")
    executable = configuration.get("executable", "omo")
    if not isinstance(executable, str) or not executable:
        raise ValueError("executable must be a nonempty string")
    supplied_tools = configuration.get("tools", ("read", "grep", "find", "ls"))
    if isinstance(supplied_tools, str) or not isinstance(supplied_tools, Iterable):
        raise TypeError("tools must be an iterable of exact tool names")
    tool_names = tuple(supplied_tools)
    if any(
        not isinstance(name, str)
        or not name
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in name
        )
        for name in tool_names
    ):
        raise ValueError("tools must contain exact alphanumeric tool names")
    limit = configuration.get("max_concurrency", 4)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("max_concurrency must be a positive integer")

    def timeout(name: str, default: float) -> float:
        value = configuration.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")
        return float(value)

    state = configuration.get("state_directory")
    if state is not None and not isinstance(state, (str, Path)):
        raise TypeError("state_directory must be a path")
    settings = Settings(
        tuple(entries),
        executable,
        tuple(dict.fromkeys(str(t) for t in tool_names)),
        limit,
        timeout("startup_timeout", 30),
        timeout("shutdown_timeout", 5),
        Path(state).expanduser().resolve() if state is not None else None,
    )
    return Harness("omo", settings.models, (), settings.backend)
