"""Optional queue operations, worker controls, and bounded log storage contracts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from anyo.observability import LogEntry as LogEntry

from ._contracts import Claim

RunControl = Literal["cancel", "force_stop"]
WorkerControl = Literal["drain", "force_stop"]


@dataclass(frozen=True)
class OperationsSettings:
    """Bound retained diagnostics separately from queue identity/settings.

    Configure the same values on receivers and workers sharing a namespace.
    Retention uses storage arrival time; timestamps are for display only.
    """

    log_max_entries: int = 10000
    log_retention_seconds: float = 86400
    max_log_bytes: int = 16384
    worker_retention_seconds: float = 86400

    def __post_init__(self) -> None:
        for name in ("log_max_entries", "max_log_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("log_retention_seconds", "worker_retention_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class WorkerInfo:
    id: str
    state: Literal["running", "draining", "stopped"] = "running"
    execution_mode: Literal["thread", "process"] = "thread"
    active_run_ids: tuple[str, ...] = ()
    started_at: float = 0
    heartbeat_at: float = 0
    hostname: str = ""
    pid: int = 0
    command: WorkerControl | None = None


@dataclass(frozen=True)
class LogPage:
    entries: list[LogEntry]
    next_cursor: int
    oldest_cursor: int | None
    gap: bool = False


def bounded_log(entry: LogEntry, settings: OperationsSettings) -> LogEntry:
    """Validate metadata and bound each stored entry after upstream redaction."""
    if not isinstance(entry.message, str):
        raise ValueError("log message must be a string")
    for name in ("source", "level", "run_id", "worker_id", "prompt_id"):
        value = getattr(entry, name)
        if value is not None and (not isinstance(value, str) or len(value) > 512):
            raise ValueError(f"log {name} must be at most 512 characters")
    if not isinstance(entry.timestamp, (int, float)) or not math.isfinite(entry.timestamp):
        raise ValueError("log timestamp must be finite")
    if entry.attempt is not None and (type(entry.attempt) is not int or entry.attempt < 0):
        raise ValueError("log attempt must be nonnegative")
    encoded = entry.message.encode("utf-8", "replace")
    if len(encoded) > settings.max_log_bytes:
        encoded = encoded[: settings.max_log_bytes]
    return replace(entry, message=encoded.decode("utf-8", "ignore"), cursor=0)


def validate_log_query(after: int, limit: int) -> None:
    if type(after) is not int or after < 0:
        raise ValueError("log cursor must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("log limit must be between 1 and 1000")


def validate_worker(worker: WorkerInfo) -> None:
    if not worker.id or len(worker.id) > 512:
        raise ValueError("worker id must contain 1 to 512 characters")
    if worker.state not in {"running", "draining", "stopped"}:
        raise ValueError("invalid worker state")
    if worker.execution_mode not in {"thread", "process"}:
        raise ValueError("invalid execution mode")
    if len(worker.active_run_ids) > 10000 or any(len(v) > 512 for v in worker.active_run_ids):
        raise ValueError("worker active run identities exceed the supported size")
    if len(worker.hostname) > 512 or not math.isfinite(worker.started_at):
        raise ValueError("invalid worker metadata")


@runtime_checkable
class OperationsBackend(Protocol):
    """An optional extension, preserving existing third-party QueueBackend implementations.

    Commands persist independently of heartbeat updates. Run commands are tied to
    the current attempt token; repeated commands are idempotent and force escalates
    cancellation. A queued cancellation is terminal immediately. Running commands
    remain requests until their owner finishes; expired ownership is interrupted
    with ``anyo.cancel_unconfirmed``, never automatically replayed. Explicit retry
    clears controls and permits cancelled/interrupted work to run again.
    """

    def configure_operations(self, settings: OperationsSettings) -> None: ...
    async def cancel(
        self, job_id: str, *, force: bool = False, expected_token: str | None = None
    ) -> bool:
        """Request cancellation only if the optional current-token precondition matches.

        ``None`` targets the current state. A nonempty token selects exactly one
        queued revision or running attempt, even after retry resets the attempt
        counter. ``""`` matches only a terminal state with no remaining token.
        """
        ...

    async def control(self, claim: Claim) -> RunControl | None: ...
    async def worker_heartbeat(self, worker: WorkerInfo) -> None: ...
    async def workers(self) -> list[WorkerInfo]: ...
    async def worker_command(self, worker_id: str, *, force: bool = False) -> bool: ...
    async def get_worker_command(self, worker_id: str) -> WorkerControl | None: ...
    async def append_logs(self, entries: Sequence[LogEntry]) -> None: ...
    async def logs(
        self,
        *,
        after: int = 0,
        limit: int = 100,
        run_id: str | None = None,
        worker_id: str | None = None,
        source: str | None = None,
        level: str | None = None,
    ) -> LogPage: ...
