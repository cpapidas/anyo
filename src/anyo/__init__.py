"""Register event hooks and start an Anyo application."""

from anyo.agents import (
    Harness,
    PromptClient,
    PromptEvent,
    PromptResult,
    ainject_prompt,
    inject_prompt,
)
from anyo.application import Application, lifespan, serve
from anyo.hooks import Hook, HookEvent, HookResponse, RetryPolicy
from anyo.integrations.betterstack import BetterStackProvider
from anyo.integrations.github import GitHubProvider
from anyo.integrations.notion import NotionProvider
from anyo.integrations.omo import omo_harness
from anyo.integrations.opencode import opencode_harness
from anyo.integrations.redis import RedisQueue
from anyo.integrations.slack import SlackProvider
from anyo.integrations.sqlite import SqliteQueue

__all__ = [
    "Hook",
    "HookEvent",
    "HookResponse",
    "Application",
    "create_application",
    "add_hook",
    "asgi",
    "lifespan",
    "serve",
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


def create_application(*, queue_url: str = "sqlite:///.anyo/queue.sqlite3") -> Application:
    """Create an isolated registry with Anyo's built-in providers and backends.

    Construction opens no resources. Each instance owns its registrations and
    lifecycle; use distinct queue URLs/namespaces for independent durable jobs.
    Use ``Application`` directly to compose custom providers or queue factories.
    """
    return Application(
        providers=[GitHubProvider(), SlackProvider(), BetterStackProvider(), NotionProvider()],
        harnesses={"opencode": opencode_harness, "omo": omo_harness},
        queue_backends={"sqlite": SqliteQueue, "redis": RedisQueue, "rediss": RedisQueue},
        queue_url=queue_url,
    )


# This composition root selects built-in adapters; construction is inert.
_application = create_application()
add_hook = _application.add_hook
asgi = _application.asgi
start = _application.start
worker = _application.worker
harness = _application.harness
