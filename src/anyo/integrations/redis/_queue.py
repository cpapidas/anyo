"""Redis adapter for the execution-owned queue protocol."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from anyo.execution._contracts import (
    CapacityError,
    Claim,
    Job,
    JobInfo,
    Outcome,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
)
from anyo.execution._operations import (
    LogEntry,
    LoggedTask,
    LogPage,
    OperationsSettings,
    RunControl,
    WorkerControl,
    WorkerInfo,
    bounded_log,
    validate_log_query,
    validate_worker,
)
from anyo.integrations.redis._script import OPERATIONS_SCRIPT, SCRIPT


class _Client(Protocol):
    async def execute_command(self, *args: str | int | float) -> object: ...
    async def aclose(self) -> None: ...


class _ClientClass(Protocol):
    def from_url(self, url: str, **kwargs: object) -> _Client: ...


class _RedisModule(Protocol):
    Redis: _ClientClass


class _ExceptionsModule(Protocol):
    RedisError: type[Exception]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise QueueMismatch("Redis queue contains an invalid record")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise QueueMismatch("Redis queue contains an invalid string")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise QueueMismatch("Redis queue contains an invalid number")
    return float(value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise QueueMismatch("Redis queue contains an invalid integer")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_number(value: object) -> float | None:
    return None if value is None else _number(value)


def _record(value: object) -> dict[str, object]:
    try:
        return _mapping(json.loads(_string(value)))
    except (ValueError, TypeError) as exc:
        raise QueueMismatch("Redis queue contains invalid JSON") from exc


def _job(value: object) -> Job:
    raw = _mapping(value)
    interrupted = raw.get("retry_interrupted")
    if type(interrupted) is not bool:
        raise QueueMismatch("Redis queue contains an invalid retry policy")
    return Job(
        id=_string(raw.get("id")),
        hook_id=_string(raw.get("hook_id")),
        fingerprint=_string(raw.get("fingerprint")),
        event=_string(raw.get("event")),
        max_attempts=_integer(raw.get("max_attempts")),
        retry_delay=_number(raw.get("retry_delay")),
        retry_interrupted=interrupted,
    )


class RedisQueue:
    """Durable job storage on a standalone Redis server (6.2 or later).

    Construction is inert. ``open`` loads the optional ``anyo[redis]`` dependency
    and pins a namespace's registry and shared limits. A Redis URL accepts the
    ``redis://`` or ``rediss://`` scheme, credentials, and a database path; query
    parameters are rejected so they cannot override operation timeouts. Use a
    persistence policy appropriate for your loss tolerance and ``noeviction``.
    Cluster and Sentinel discovery are not supported.
    """

    def __init__(self, url: str, namespace: str, settings: QueueSettings) -> None:
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme in {"redis", "rediss"}
                and parsed.hostname is not None
                and not parsed.query
                and not parsed.fragment
                and (parsed.path in {"", "/"} or parsed.path[1:].isdigit())
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except (ValueError, AttributeError) as exc:
            raise ValueError("Invalid Redis queue URL") from exc
        if not valid:
            raise ValueError("Use a redis:// or rediss:// URL with a database path and no query")
        if not isinstance(namespace, str) or not namespace or len(namespace.encode()) > 512:
            raise ValueError("queue namespace must contain 1 to 512 UTF-8 bytes")
        self._url = url
        self._settings = settings
        prefix = f"anyo:queue:{{{hashlib.sha256(namespace.encode()).hexdigest()}}}"
        self._keys = tuple(
            f"{prefix}:{suffix}"
            for suffix in ("metadata", "jobs", "queued", "running", "terminal", "all")
        )
        self._operations_keys = self._keys + tuple(
            f"{prefix}:{suffix}"
            for suffix in (
                "workers",
                "worker_times",
                "logs",
                "log_order",
                "log_times",
                "log_cursor",
                "log_pruned",
            )
        )
        self._operations = OperationsSettings()
        self._client: _Client | None = None
        self._operation_slots: asyncio.Semaphore | None = None
        self._redis_error: type[Exception] = OSError
        self._signature: str | None = None
        self._registry: dict[str, str] = {}

    def configure_operations(self, settings: OperationsSettings) -> None:
        self._operations = settings

    async def open(self, registry: Mapping[str, str]) -> None:
        """Connect and validate the namespace without starting any worker."""
        if self._client is not None:
            raise RuntimeError("Redis queue is already open")
        if any(
            not key or not value or not isinstance(key, str) or not isinstance(value, str)
            for key, value in registry.items()
        ):
            raise ValueError("Queue registry must map nonempty hook IDs to fingerprints")
        try:
            module = cast(_RedisModule, importlib.import_module("redis.asyncio"))
            exceptions = cast(_ExceptionsModule, importlib.import_module("redis.exceptions"))
        except ModuleNotFoundError as exc:
            if exc.name == "redis" or (exc.name and exc.name.startswith("redis.")):
                raise ImportError(
                    "Redis queues require the optional extra: pip install 'anyo[redis]'"
                ) from exc
            raise
        self._redis_error = exceptions.RedisError
        self._registry = dict(registry)
        self._signature = _json(
            {
                "schema": 1,
                "registry": self._registry,
                "capacity": self._settings.capacity,
                "max_records": self._settings.max_records,
                "retention_seconds": float(self._settings.retention_seconds),
            }
        )
        self._client = module.Redis.from_url(
            self._url,
            decode_responses=True,
            encoding="utf-8",
            socket_timeout=self._settings.operation_timeout,
            socket_connect_timeout=self._settings.operation_timeout,
            retry_on_timeout=False,
            retry_on_error=[],
            retry=None,
            protocol=2,
            max_connections=32,
            **({"ssl_check_hostname": True} if self._url.startswith("rediss://") else {}),
        )
        self._operation_slots = asyncio.Semaphore(32)
        try:
            info = _mapping(await self._execute("INFO", "server"))
            version = _string(info.get("redis_version"))
            parts = version.split(".")
            if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
                raise QueueMismatch("Redis server version could not be verified")
            if (int(parts[0]), int(parts[1])) < (6, 2):
                raise QueueMismatch("Redis queues require Redis server 6.2 or later")
            await self._run("open", {})
        except BaseException:
            try:
                await self.close()
            except QueueUnavailable:
                pass  # Preserve the startup failure, including cancellation.
            raise

    async def _execute(self, *args: str | int | float) -> object:
        client = self._client
        slots = self._operation_slots
        if client is None or slots is None:
            raise RuntimeError("Redis queue is not open")
        try:
            async with asyncio.timeout(self._settings.operation_timeout):
                # The Redis pool fails immediately when exhausted. Queue the
                # waiters here instead; waiting shares the operation deadline.
                async with slots:
                    return await client.execute_command(*args)
        except (self._redis_error, OSError, TimeoutError) as exc:
            raise QueueUnavailable(
                "Redis queue operation failed; its write outcome may be unknown"
            ) from exc

    async def _run(
        self, operation: str, options: object, *, operations: bool = False
    ) -> list[object]:
        if self._signature is None or self._client is None:
            raise RuntimeError("Redis queue is not open")
        keys = self._operations_keys if operations else self._keys + (self._operations_keys[6],)
        raw = await self._execute(
            "EVAL",
            OPERATIONS_SCRIPT if operations else SCRIPT,
            len(keys),
            *keys,
            self._signature,
            operation,
            _json(options),
            uuid4().hex,
        )
        if not isinstance(raw, list) or not raw:
            raise QueueMismatch("Redis queue returned an invalid response")
        response = cast(list[object], raw)
        if response[0] == "capacity":
            raise CapacityError("Redis queue capacity or retained history is full")
        if response[0] == "permission":
            raise QueueUnavailable("Redis queue account lacks required command or key permissions")
        if response[0] == "mismatch":
            raise QueueMismatch(
                "Redis queue schema, registry, settings, or records are incompatible"
            )
        if response[0] != "ok":
            raise QueueMismatch("Redis queue returned an unknown response")
        return response[1:]

    async def admit(self, jobs: Sequence[Job]) -> int:
        payload: list[dict[str, object]] = []
        for job in jobs:
            if not all(
                isinstance(value, str) and value for value in (job.id, job.hook_id, job.fingerprint)
            ):
                raise ValueError("Queue job identifiers must be nonempty strings")
            if not isinstance(job.event, str):
                raise ValueError("Queue job event must be a serialized string")
            if type(job.max_attempts) is not int or not 1 <= job.max_attempts <= 2**53 - 1:
                raise ValueError("max_attempts must be a positive, exactly representable integer")
            if (
                isinstance(job.retry_delay, bool)
                or not isinstance(job.retry_delay, (int, float))
                or not math.isfinite(job.retry_delay)
                or job.retry_delay < 0
            ):
                raise ValueError("retry_delay must be finite and nonnegative")
            if type(job.retry_interrupted) is not bool:
                raise ValueError("retry_interrupted must be a bool")
            definition = asdict(job)
            definition["retry_delay"] = float(job.retry_delay)
            # Delivery identity owns the original accepted payload. Providers may
            # change retry metadata or JSON key order on a subsequent redelivery.
            compatible = {key: value for key, value in definition.items() if key != "event"}
            payload.append({"job": definition, "definition": _json(compatible)})
        response = await self._run("admit", {"jobs": payload})
        return _integer(response[0])

    async def claim(self, owner: str) -> Claim | None:
        if not isinstance(owner, str) or not owner:
            raise ValueError("claim owner must be a nonempty string")
        response = await self._run(
            "claim",
            {
                "owner": owner,
                "token": uuid4().hex,
                "lease_seconds": self._settings.lease_seconds,
            },
        )
        if not response:
            return None
        record = _record(response[0])
        return Claim(
            job=_job(record.get("job")),
            token=_string(record.get("token")),
            attempt=_integer(record.get("attempt")),
        )

    async def renew(self, claim: Claim) -> bool:
        response = await self._run(
            "renew",
            {
                "id": claim.job.id,
                "token": claim.token,
                "attempt": claim.attempt,
                "lease_seconds": self._settings.lease_seconds,
            },
        )
        return _integer(response[0]) == 1

    async def finish(
        self, claim: Claim, status: Outcome, *, error: str | None = None, retry: bool = False
    ) -> bool:
        if status not in {"successful", "failed", "interrupted", "cancelled"}:
            raise ValueError("Invalid queue completion status")
        if error is not None and (not isinstance(error, str) or len(error) > 256):
            raise ValueError("Queue errors must be short error codes")
        response = await self._run(
            "finish",
            {
                "id": claim.job.id,
                "token": claim.token,
                "attempt": claim.attempt,
                "status": status,
                "error": error,
                "retry": retry,
            },
        )
        return _integer(response[0]) == 1

    async def inspect(self, *, limit: int = 100) -> list[JobInfo]:
        if type(limit) is not int or limit < 1:
            raise ValueError("inspect limit must be a positive integer")
        response = await self._run("inspect", {"limit": limit})
        result: list[JobInfo] = []
        for item in response:
            record = _record(item)
            status = record.get("status")
            if status not in {
                "queued",
                "running",
                "successful",
                "failed",
                "interrupted",
                "cancelled",
            }:
                raise QueueMismatch("Redis queue contains an invalid status")
            lease, error = record.get("lease_until"), record.get("error")
            result.append(
                JobInfo(
                    id=_string(record.get("id")),
                    hook_id=_string(_mapping(record.get("job")).get("hook_id")),
                    status=status,
                    attempt=_integer(record.get("attempt")),
                    available_at=_number(record.get("available_at")),
                    lease_until=None if lease is None else _number(lease),
                    error=None if error is None else _string(error),
                    owner=_optional_string(record.get("owner")),
                    created_at=_optional_number(record.get("created_at")),
                    started_at=_optional_number(record.get("started_at")),
                    finished_at=_optional_number(record.get("completed_at")),
                    control=cast(RunControl | None, _optional_string(record.get("control"))),
                    control_requested_at=_optional_number(record.get("control_requested_at")),
                    attempt_token=_optional_string(record.get("token")),
                )
            )
        return result

    async def retry(self, job_id: str) -> bool:
        response = await self._run("retry", {"id": job_id})
        return _integer(response[0]) == 1

    async def cancel(
        self, job_id: str, *, force: bool = False, expected_token: str | None = None
    ) -> bool:
        response = await self._run(
            "cancel", {"id": job_id, "force": force, "expected_token": expected_token}
        )
        return _integer(response[0]) == 1

    async def control(self, claim: Claim) -> RunControl | None:
        response = await self._run(
            "control", {"id": claim.job.id, "token": claim.token, "attempt": claim.attempt}
        )
        return cast(RunControl, _string(response[0])) if response else None

    async def _operate(self, operation: str, options: dict[str, object]) -> list[object]:
        return await self._run(
            operation, {**options, "settings": asdict(self._operations)}, operations=True
        )

    async def worker_heartbeat(self, worker: WorkerInfo) -> None:
        validate_worker(worker)
        await self._operate("worker_heartbeat", {"worker": asdict(worker)})

    async def workers(self) -> list[WorkerInfo]:
        response = await self._operate("workers", {})
        result: list[WorkerInfo] = []
        for item in response:
            data = _record(item)
            active = data.get("active_run_ids")
            # Redis Lua's cjson serializes an empty array as an empty object.
            if not isinstance(active, list) and active != {}:
                raise QueueMismatch("Redis worker contains invalid active run identities")
            state, mode = data.get("state"), data.get("execution_mode")
            if state not in {"running", "draining", "stopped"} or mode not in {"thread", "process"}:
                raise QueueMismatch("Redis worker contains invalid lifecycle state")
            result.append(
                WorkerInfo(
                    id=_string(data.get("id")),
                    state=state,
                    execution_mode=mode,
                    active_run_ids=tuple(_string(value) for value in active),
                    started_at=_number(data.get("started_at")),
                    heartbeat_at=_number(data.get("heartbeat_at")),
                    hostname=_string(data.get("hostname")),
                    pid=_integer(data.get("pid")),
                    command=cast(WorkerControl | None, _optional_string(data.get("command"))),
                )
            )
        return result

    async def worker_command(self, worker_id: str, *, force: bool = False) -> bool:
        response = await self._operate("worker_command", {"id": worker_id, "force": force})
        return _integer(response[0]) == 1

    async def get_worker_command(self, worker_id: str) -> WorkerControl | None:
        response = await self._operate("get_worker_command", {"id": worker_id})
        return cast(WorkerControl, _string(response[0])) if response else None

    async def append_logs(self, entries: Sequence[LogEntry]) -> None:
        if len(entries) > 1000:
            raise ValueError("append at most 1000 logs per batch")
        if not entries:
            return
        await self._operate(
            "append_logs",
            {"entries": [asdict(bounded_log(entry, self._operations)) for entry in entries]},
        )

    async def logged_tasks(self) -> list[LoggedTask]:
        response = await self._operate("logged_tasks", {})
        tasks = []
        for raw in response:
            task = _record(raw)
            tasks.append(
                LoggedTask(
                    _string(task.get("run_id")),
                    _integer(task.get("entry_count")),
                    _integer(task.get("last_cursor")),
                    _number(task.get("last_timestamp")),
                    _optional_string(task.get("hook_id")),
                )
            )
        return sorted(tasks, key=lambda task: task.last_cursor, reverse=True)

    async def logs(
        self,
        *,
        after: int = 0,
        limit: int = 100,
        run_id: str | None = None,
        worker_id: str | None = None,
        source: str | None = None,
        level: str | None = None,
    ) -> LogPage:
        validate_log_query(after, limit)
        response = await self._operate(
            "logs",
            {
                "after": after,
                "limit": limit,
                "run_id": run_id,
                "worker_id": worker_id,
                "source": source,
                "level": level,
            },
        )
        data = _record(response[0])
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list) and raw_entries != {}:
            raise QueueMismatch("Redis logs contain invalid entries")
        entries: list[LogEntry] = []
        for raw in raw_entries:
            entry = _mapping(raw)
            attempt = entry.get("attempt")
            entries.append(
                LogEntry(
                    message=_string(entry.get("message")),
                    source=_string(entry.get("source")),
                    level=_string(entry.get("level")),
                    timestamp=_number(entry.get("timestamp")),
                    run_id=_optional_string(entry.get("run_id")),
                    attempt=None if attempt is None else _integer(attempt),
                    worker_id=_optional_string(entry.get("worker_id")),
                    prompt_id=_optional_string(entry.get("prompt_id")),
                    cursor=_integer(entry.get("cursor")),
                    hook_id=_optional_string(entry.get("hook_id")),
                )
            )
        oldest = data.get("oldest_cursor")
        return LogPage(
            entries,
            _integer(data.get("next_cursor")),
            None if oldest is None else _integer(oldest),
            bool(data.get("gap")),
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        self._operation_slots = None
        self._signature = None
        if client is not None:
            try:
                async with asyncio.timeout(self._settings.operation_timeout):
                    await client.aclose()
            except (self._redis_error, OSError, TimeoutError) as exc:
                raise QueueUnavailable("Redis queue connection could not be closed") from exc
