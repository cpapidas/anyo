"""Register event hooks and start an Anyo application."""

from anyo.agents import (
    Harness,
    PromptClient,
    PromptEvent,
    PromptResult,
    ainject_prompt,
    inject_prompt,
)
from anyo.application import Application
from anyo.hooks import Hook, HookEvent, HookResponse, RetryPolicy
from anyo.integrations.betterstack import BetterStackProvider
from anyo.integrations.github import GitHubProvider
from anyo.integrations.omo import omo_harness
from anyo.integrations.opencode import opencode_harness
from anyo.integrations.redis import RedisQueue
from anyo.integrations.slack import SlackProvider
from anyo.integrations.sqlite import SqliteQueue

__all__ = [
    "Hook",
    "HookEvent",
    "HookResponse",
    "add_hook",
    "start",
    "worker",
    "RetryPolicy",
    "harness",
    "Harness",
    "PromptClient",
    "PromptEvent",
    "PromptResult",
    "inject_prompt",
    "ainject_prompt",
]

# This composition root selects built-in adapters; construction is inert.
_application = Application(
    providers=[GitHubProvider(), SlackProvider(), BetterStackProvider()],
    harnesses={"opencode": opencode_harness, "omo": omo_harness},
    queue_backends={"sqlite": SqliteQueue, "redis": RedisQueue, "rediss": RedisQueue},
    queue_url="sqlite:///.anyo/queue.sqlite3",
)
add_hook = _application.add_hook
start = _application.start
worker = _application.worker
harness = _application.harness
