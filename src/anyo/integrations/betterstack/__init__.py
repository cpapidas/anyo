"""Better Stack incident and error webhooks; no outbound client is required."""

from anyo.integrations.betterstack._events import BetterStackEvent
from anyo.integrations.betterstack._provider import BetterStackProvider

__all__ = ["BetterStackEvent", "BetterStackProvider"]
