"""Storage-independent durable queue contracts. No I/O happens at construction."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Status = Literal["queued", "running", "successful", "failed", "interrupted", "cancelled"]
Outcome = Literal["successful", "failed", "interrupted", "cancelled"]


class CapacityError(RuntimeError):
    """Queue capacity or retained history is full."""


class QueueUnavailable(RuntimeError):
    """Storage is unavailable; the outcome of a write may be uncertain."""


class QueueMismatch(ValueError):
    """Namespace schema, configuration, or registered hooks do not match."""


@dataclass(frozen=True)
class QueueSettings:
    capacity: int = 100
    max_records: int = 10000
    retention_seconds: float = 3600
    lease_seconds: float = 30
    poll_interval: float = 0.1
    operation_timeout: float = 2

    def __post_init__(self) -> None:
        for name in ("capacity", "max_records"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("retention_seconds", "lease_seconds", "poll_interval", "operation_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be finite and positive")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class Job:
    id: str
    hook_id: str
    fingerprint: str
    event: str = field(repr=False)
    max_attempts: int = 1
    retry_delay: float = 1
    retry_interrupted: bool = False


@dataclass(frozen=True)
class Claim:
    job: Job
    token: str
    attempt: int


@dataclass(frozen=True)
class JobInfo:
    id: str
    hook_id: str
    status: Status
    attempt: int
    available_at: float
    lease_until: float | None = None
    error: str | None = None
    owner: str | None = None
    created_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    control: Literal["cancel", "force_stop"] | None = None
    control_requested_at: float | None = None
    attempt_token: str | None = None


class QueueBackend(Protocol):
    """A namespace is shared by a complete, identical registry and queue limits.

    open pins registry fingerprints and capacity/history settings in a schema-1
    namespace. Mismatches raise QueueMismatch (choose a new namespace on changes).
    Methods are atomic. Backends use their own wall clock, never loop.time().
    Completed history expires on maintenance; active records never expire.
    Lease expiry retries only with retry_interrupted and attempts remaining;
    otherwise it records interrupted. Failures use error codes, never messages.
    """

    async def open(self, registry: Mapping[str, str]) -> None: ...
    async def admit(self, jobs: Sequence[Job]) -> int:
        """Deduplicate active/successful/cancelled; reset failed/interrupted on redelivery.

        Interrupted/failed attempts with pending operator commands stay deduplicated.

        Atomically enforce capacity (queued+running) and max_records for the
        entire new group. Preserve the first accepted event when redelivery
        changes transport metadata. New/redelivered attempts begin at zero. Prune history
        and recover expired leases first. Mismatched job definitions fail.
        """
        ...

    async def claim(self, owner: str) -> Claim | None:
        """Recover expired leases, then claim earliest available queued job.

        Increment attempt and issue a fresh unique token. No destructive dequeue.
        """
        ...

    async def renew(self, claim: Claim) -> bool:
        """Extend a still-live lease iff its current token matches."""
        ...

    async def finish(
        self, claim: Claim, status: Outcome, *, error: str | None = None, retry: bool = False
    ) -> bool:
        """Fence live token; optionally queue failed/interrupted with bounded delay.

        Retry only when retry=True and attempt < max_attempts; interrupted also
        requires job.retry_interrupted. Delay is retry_delay * 2**(attempt-1),
        capped at 3600 seconds. Successful/cancelled or pending operator controls never
        retry. Otherwise terminal.
        """
        ...

    async def inspect(self, *, limit: int = 100) -> list[JobInfo]: ...
    async def retry(self, job_id: str) -> bool:
        """Explicitly reset failed/interrupted/cancelled work, subject to capacity."""
        ...

    async def close(self) -> None: ...


class QueueFactory(Protocol):
    def __call__(self, url: str, namespace: str, settings: QueueSettings) -> QueueBackend: ...
