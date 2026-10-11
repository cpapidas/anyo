"""SQLite queue transactions, kept outside the application's event loop."""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast
from urllib.parse import unquote

from anyo.execution._contracts import (
    CapacityError,
    Claim,
    Job,
    JobInfo,
    Outcome,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
    Status,
)
from anyo.execution._operations import (
    LogEntry,
    LogPage,
    OperationsSettings,
    RunControl,
    WorkerControl,
    WorkerInfo,
    bounded_log,
    validate_log_query,
    validate_worker,
)

if TYPE_CHECKING:
    import sqlite3

_T = TypeVar("_T")
_SCHEMA_VERSION = 1


class SqliteQueue:
    """Durable queue on one host, with no external service or Python dependency.

    ``sqlite:///queue.db`` is relative to the current directory;
    ``sqlite:////absolute/queue.db`` is absolute. Parent directories are created
    at open. ``sqlite:///:memory:`` creates a private, ephemeral queue.

    Each instance owns one connection, serialized in worker threads. Processes
    coordinate with SQLite transactions. DELETE journaling and FULL synchronous
    writes avoid depending on a particular runtime's WAL fixes. Local filesystems
    only: sharing the file over a network filesystem is unsupported.
    """

    def __init__(self, url: str, namespace: str, settings: QueueSettings) -> None:
        if not url.startswith("sqlite:///") or "?" in url or "#" in url:
            raise ValueError("SQLite URL must be sqlite:///path without query or fragment")
        path = unquote(url[len("sqlite:///") :])
        if not path or "\x00" in path:
            raise ValueError("SQLite URL must contain a database path")
        if not namespace or len(namespace.encode("utf-8")) > 512:
            raise ValueError("queue namespace must contain 1 to 512 UTF-8 bytes")
        self._path = path
        self._namespace = namespace
        self._settings = settings
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._registry: dict[str, str] | None = None
        self._closed = False
        self._operations = OperationsSettings()

    def configure_operations(self, settings: OperationsSettings) -> None:
        self._operations = settings

    async def open(self, registry: Mapping[str, str]) -> None:
        """Initialize storage and verify the namespace's pinned configuration."""
        registry = dict(registry)
        if any(not key or not value for key, value in registry.items()):
            raise ValueError("registry hook IDs and fingerprints must be nonempty")
        await self._invoke(lambda connection: self._initialize(connection, registry), opening=True)
        self._registry = registry

    async def admit(self, jobs: Sequence[Job]) -> int:
        """Commit a complete group, preserving active and successful attempts."""
        snapshots = tuple(jobs)
        for job in snapshots:
            self._validate_job(job)
        return await self._invoke(lambda connection: self._admit(connection, snapshots))

    async def claim(self, owner: str) -> Claim | None:
        """Atomically lease the earliest eligible queued job to this worker."""
        if not owner:
            raise ValueError("worker owner must be nonempty")

        def claim(connection: sqlite3.Connection) -> Claim | None:
            now = self._now(connection)
            self._maintain(connection, now)
            worker = connection.execute(
                "SELECT command,record FROM anyo_workers WHERE namespace=? AND id IN (?,?) "
                "ORDER BY id=? DESC LIMIT 1",
                (self._namespace, owner, owner.rsplit(":", 1)[0], owner),
            ).fetchone()
            if worker is not None and (
                worker["command"] is not None or json.loads(worker["record"])["state"] != "running"
            ):
                return None
            row = connection.execute(
                "SELECT * FROM anyo_jobs WHERE namespace=? AND status='queued' "
                "AND available_at<=? ORDER BY available_at, sequence LIMIT 1",
                (self._namespace, now),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            attempt = int(row["attempt"]) + 1
            connection.execute(
                "UPDATE anyo_jobs SET status='running', attempt=?, owner=?, token=?, "
                "lease_until=?, finished_at=NULL, started_at=? WHERE namespace=? AND id=?",
                (
                    attempt,
                    owner,
                    token,
                    now + self._settings.lease_seconds,
                    now,
                    self._namespace,
                    row["id"],
                ),
            )
            return Claim(self._job(row), token, attempt)

        return await self._invoke(claim)

    async def renew(self, claim: Claim) -> bool:
        """Renew only the current, unexpired attempt token."""

        def renew(connection: sqlite3.Connection) -> bool:
            now = self._now(connection)
            cursor = connection.execute(
                "UPDATE anyo_jobs SET lease_until=? WHERE namespace=? AND id=? "
                "AND status='running' AND token=? AND attempt=? AND lease_until>?",
                (
                    now + self._settings.lease_seconds,
                    self._namespace,
                    claim.job.id,
                    claim.token,
                    claim.attempt,
                    now,
                ),
            )
            return cursor.rowcount == 1

        return await self._invoke(renew)

    async def finish(
        self, claim: Claim, status: Outcome, *, error: str | None = None, retry: bool = False
    ) -> bool:
        """Finish a live attempt, or schedule its explicitly permitted retry."""
        if status not in ("successful", "failed", "interrupted", "cancelled"):
            raise ValueError("invalid terminal job status")
        if error is not None and (not isinstance(error, str) or len(error) > 256):
            raise ValueError("job error must be a code of at most 256 characters")

        def finish(connection: sqlite3.Connection) -> bool:
            now = self._now(connection)
            row = connection.execute(
                "SELECT * FROM anyo_jobs WHERE namespace=? AND id=? AND status='running' "
                "AND token=? AND attempt=? AND lease_until>?",
                (self._namespace, claim.job.id, claim.token, claim.attempt, now),
            ).fetchone()
            if row is None:
                return False
            replay = (
                retry
                and status not in ("successful", "cancelled")
                and row["control"] is None
                and row["attempt"] < row["max_attempts"]
                and (status != "interrupted" or row["retry_interrupted"])
            )
            next_status = "queued" if replay else status
            available = now + self._delay(row) if replay else row["available_at"]
            connection.execute(
                "UPDATE anyo_jobs SET status=?, available_at=?, finished_at=?, "
                "owner=NULL, token=?, lease_until=NULL, error=? WHERE namespace=? AND id=?",
                (
                    next_status,
                    available,
                    None if replay else now,
                    uuid.uuid4().hex if replay else None,
                    error,
                    self._namespace,
                    claim.job.id,
                ),
            )
            return True

        return await self._invoke(finish)

    async def inspect(self, *, limit: int = 100) -> list[JobInfo]:
        """Return oldest admitted records, after recovering expired attempts."""
        if type(limit) is not int or limit < 1:
            raise ValueError("inspection limit must be a positive integer")

        def inspect(connection: sqlite3.Connection) -> list[JobInfo]:
            self._maintain(connection, self._now(connection))
            rows = connection.execute(
                "SELECT * FROM anyo_jobs WHERE namespace=? ORDER BY sequence LIMIT ?",
                (self._namespace, limit),
            ).fetchall()
            return [
                JobInfo(
                    row["id"],
                    row["hook_id"],
                    cast(Status, row["status"]),
                    row["attempt"],
                    row["available_at"],
                    row["lease_until"],
                    row["error"],
                    row["owner"],
                    row["created_at"],
                    row["started_at"],
                    row["finished_at"],
                    row["control"],
                    row["control_requested_at"],
                    row["token"],
                )
                for row in rows
            ]

        return await self._invoke(inspect)

    async def retry(self, job_id: str) -> bool:
        """Reset a failed/interrupted job for explicit consumer-authorized replay."""

        def retry(connection: sqlite3.Connection) -> bool:
            now = self._now(connection)
            self._maintain(connection, now)
            row = connection.execute(
                "SELECT status FROM anyo_jobs WHERE namespace=? AND id=?",
                (self._namespace, job_id),
            ).fetchone()
            if row is None or row["status"] not in ("failed", "interrupted", "cancelled"):
                return False
            if self._active_count(connection) >= self._settings.capacity:
                raise CapacityError("queue capacity is full")
            self._reset(connection, job_id, now)
            return True

        return await self._invoke(retry)

    async def cancel(
        self, job_id: str, *, force: bool = False, expected_token: str | None = None
    ) -> bool:
        """Cancel queued work or persist a command on the current running attempt."""

        def cancel(connection: sqlite3.Connection) -> bool:
            now = self._now(connection)
            self._maintain(connection, now)
            row = connection.execute(
                "SELECT status, control, token FROM anyo_jobs WHERE namespace=? AND id=?",
                (self._namespace, job_id),
            ).fetchone()
            if row is None or row["status"] not in ("queued", "running", "cancelled"):
                return False
            if expected_token is not None and (row["token"] or "") != expected_token:
                return False
            if row["status"] == "cancelled":
                return True
            command = "force_stop" if force or row["control"] == "force_stop" else "cancel"
            connection.execute(
                "UPDATE anyo_jobs SET control=?, control_requested_at=COALESCE("
                "control_requested_at, ?), status=CASE WHEN status='queued' THEN 'cancelled' "
                "ELSE status END, finished_at=CASE WHEN status='queued' "
                "THEN ? ELSE finished_at END, "
                "token=CASE WHEN status='queued' THEN NULL ELSE token END "
                "WHERE namespace=? AND id=?",
                (command, now, now, self._namespace, job_id),
            )
            return True

        return await self._invoke(cancel)

    async def control(self, claim: Claim) -> RunControl | None:
        def control(connection: sqlite3.Connection) -> RunControl | None:
            row = connection.execute(
                "SELECT control FROM anyo_jobs WHERE namespace=? AND id=? AND status='running' "
                "AND token=? AND attempt=? AND lease_until>?",
                (self._namespace, claim.job.id, claim.token, claim.attempt, self._now(connection)),
            ).fetchone()
            return cast(RunControl | None, row["control"] if row else None)

        return await self._invoke(control)

    def _prune_workers(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "DELETE FROM anyo_workers WHERE namespace=? AND heartbeat_at<?",
            (self._namespace, now - self._operations.worker_retention_seconds),
        )

    async def worker_heartbeat(self, worker: WorkerInfo) -> None:
        validate_worker(worker)

        def heartbeat(connection: sqlite3.Connection) -> None:
            now = self._now(connection)
            self._prune_workers(connection, now)
            record = asdict(worker)
            record["heartbeat_at"] = now
            record["command"] = None
            connection.execute(
                "INSERT INTO anyo_workers(namespace,id,record,heartbeat_at) VALUES(?,?,?,?) "
                "ON CONFLICT(namespace,id) DO UPDATE SET record=excluded.record, "
                "heartbeat_at=excluded.heartbeat_at",
                (self._namespace, worker.id, json.dumps(record), now),
            )

        await self._invoke(heartbeat)

    async def workers(self) -> list[WorkerInfo]:
        def workers(connection: sqlite3.Connection) -> list[WorkerInfo]:
            self._prune_workers(connection, self._now(connection))
            result: list[WorkerInfo] = []
            for row in connection.execute(
                "SELECT record,command FROM anyo_workers WHERE namespace=? ORDER BY id",
                (self._namespace,),
            ):
                data = json.loads(row["record"])
                data["command"] = row["command"]
                data["active_run_ids"] = tuple(data["active_run_ids"])
                result.append(WorkerInfo(**data))
            return result

        return await self._invoke(workers)

    async def worker_command(self, worker_id: str, *, force: bool = False) -> bool:
        def command(connection: sqlite3.Connection) -> bool:
            self._prune_workers(connection, self._now(connection))
            row = connection.execute(
                "SELECT record FROM anyo_workers WHERE namespace=? AND id=?",
                (self._namespace, worker_id),
            ).fetchone()
            if row is None or json.loads(row["record"])["state"] == "stopped":
                return False
            connection.execute(
                "UPDATE anyo_workers SET command=CASE WHEN command='force_stop' THEN command "
                "ELSE ? END WHERE namespace=? AND id=?",
                ("force_stop" if force else "drain", self._namespace, worker_id),
            )
            if force:
                prefix = worker_id + ":"
                connection.execute(
                    "UPDATE anyo_jobs SET control='force_stop', "
                    "control_requested_at=COALESCE(control_requested_at,?) "
                    "WHERE namespace=? AND status='running' "
                    "AND (owner=? OR substr(owner,1,?)=?)",
                    (self._now(connection), self._namespace, worker_id, len(prefix), prefix),
                )
            return True

        return await self._invoke(command)

    async def get_worker_command(self, worker_id: str) -> WorkerControl | None:
        def command(connection: sqlite3.Connection) -> WorkerControl | None:
            row = connection.execute(
                "SELECT command FROM anyo_workers WHERE namespace=? AND id=?",
                (self._namespace, worker_id),
            ).fetchone()
            return cast(WorkerControl | None, row["command"] if row else None)

        return await self._invoke(command)

    def _prune_logs(self, connection: sqlite3.Connection, now: float) -> None:
        # Both policies remove an insertion-order prefix, making cursor gaps precise.
        cursor = connection.execute(
            "SELECT cursor FROM anyo_log_cursors WHERE namespace=?", (self._namespace,)
        ).fetchone()[0]
        expired = connection.execute(
            "SELECT COALESCE(MAX(cursor),0) FROM anyo_logs WHERE namespace=? AND received_at<=?",
            (self._namespace, now - self._operations.log_retention_seconds),
        ).fetchone()[0]
        cutoff = max(expired, cursor - self._operations.log_max_entries)
        connection.execute(
            "DELETE FROM anyo_logs WHERE namespace=? AND cursor<=?", (self._namespace, cutoff)
        )
        connection.execute(
            "UPDATE anyo_log_cursors SET pruned_cursor=MAX(pruned_cursor,?) WHERE namespace=?",
            (cutoff, self._namespace),
        )

    async def append_logs(self, entries: Sequence[LogEntry]) -> None:
        snapshots = [bounded_log(entry, self._operations) for entry in entries]
        if not snapshots:
            return
        if len(snapshots) > 1000:
            raise ValueError("append at most 1000 logs per batch")

        def append(connection: sqlite3.Connection) -> None:
            now = self._now(connection)
            cursor = connection.execute(
                "SELECT cursor FROM anyo_log_cursors WHERE namespace=?", (self._namespace,)
            ).fetchone()[0]
            for entry in snapshots:
                cursor += 1
                record = asdict(entry)
                record["cursor"] = cursor
                connection.execute(
                    "INSERT INTO anyo_logs VALUES(?,?,?,?,?,?,?,?)",
                    (
                        self._namespace,
                        cursor,
                        now,
                        json.dumps(record),
                        entry.run_id,
                        entry.worker_id,
                        entry.source,
                        entry.level,
                    ),
                )
            connection.execute(
                "UPDATE anyo_log_cursors SET cursor=? WHERE namespace=?",
                (cursor, self._namespace),
            )
            self._prune_logs(connection, now)

        await self._invoke(append)

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

        def logs(connection: sqlite3.Connection) -> LogPage:
            self._prune_logs(connection, self._now(connection))
            state = connection.execute(
                "SELECT cursor,pruned_cursor FROM anyo_log_cursors WHERE namespace=?",
                (self._namespace,),
            ).fetchone()
            oldest = connection.execute(
                "SELECT MIN(cursor) FROM anyo_logs WHERE namespace=?", (self._namespace,)
            ).fetchone()[0]
            query = "SELECT record FROM anyo_logs WHERE namespace=? AND cursor>?"
            values: list[str | int] = [self._namespace, after]
            for name, value in (
                ("run_id", run_id),
                ("worker_id", worker_id),
                ("source", source),
                ("level", level),
            ):
                if value is not None:
                    query += f" AND {name}=?"
                    values.append(value)
            query += " ORDER BY cursor LIMIT ?"
            values.append(limit)
            entries = [LogEntry(**json.loads(row[0])) for row in connection.execute(query, values)]
            next_cursor = (
                entries[-1].cursor if len(entries) == limit else max(after, state["cursor"])
            )
            return LogPage(entries, next_cursor, oldest, after < state["pruned_cursor"])

        return await self._invoke(logs)

    async def close(self) -> None:
        """Wait for outstanding bounded operations and close the owned connection."""

        def close() -> None:
            with self._lock:
                self._closed = True
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None

        task = asyncio.create_task(asyncio.to_thread(close))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _invoke(
        self, operation: Callable[[sqlite3.Connection], _T], *, opening: bool = False
    ) -> _T:
        cancelled = threading.Event()
        deadline = time.monotonic() + self._settings.operation_timeout
        try:
            return await asyncio.to_thread(self._run, operation, opening, deadline, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            if opening:
                await self.close()
            raise

    def _run(
        self,
        operation: Callable[[sqlite3.Connection], _T],
        opening: bool,
        deadline: float,
        cancelled: threading.Event,
    ) -> _T:
        try:
            import sqlite3
        except ImportError as exc:
            raise QueueUnavailable(
                "SQLite support is missing; install Python with sqlite3"
            ) from exc

        def check() -> None:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise QueueUnavailable("SQLite queue operation exceeded its deadline")

        if not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise QueueUnavailable("SQLite queue operation exceeded its deadline")
        created = False
        succeeded = False
        try:
            check()
            if self._closed:
                raise QueueUnavailable("SQLite queue is closed")
            if opening:
                if self._connection is not None:
                    raise RuntimeError("SQLite queue can only be opened once")
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(
                    self._path,
                    timeout=max(0, deadline - time.monotonic()),
                    isolation_level=None,
                    check_same_thread=False,
                )
                created = True
                self._connection.row_factory = sqlite3.Row
            connection = self._connection
            if connection is None:
                raise QueueUnavailable("SQLite queue must be opened before use")
            connection.set_progress_handler(
                lambda: int(cancelled.is_set() or time.monotonic() >= deadline), 1000
            )
            connection.execute(
                f"PRAGMA busy_timeout={max(1, int((deadline - time.monotonic()) * 1000))}"
            )
            if opening:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                check()
                connection.execute(
                    f"PRAGMA busy_timeout={max(1, int((deadline - time.monotonic()) * 1000))}"
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            succeeded = True
            return result
        except (sqlite3.Error, OSError) as exc:
            raise QueueUnavailable(
                "SQLite queue unavailable; verify the local database path, permissions, and locks"
            ) from exc
        finally:
            if self._connection is not None:
                self._connection.set_progress_handler(None, 0)
                if created and not succeeded:
                    self._connection.close()
                    self._connection = None
            self._lock.release()

    def _initialize(self, connection: sqlite3.Connection, registry: dict[str, str]) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS anyo_namespaces (namespace TEXT PRIMARY KEY, "
            "schema_version INTEGER NOT NULL, configuration TEXT NOT NULL)"
        )
        configuration = json.dumps(
            {
                "registry": registry,
                "capacity": self._settings.capacity,
                "max_records": self._settings.max_records,
                "retention_seconds": float(self._settings.retention_seconds),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        row = connection.execute(
            "SELECT schema_version, configuration FROM anyo_namespaces WHERE namespace=?",
            (self._namespace,),
        ).fetchone()
        if row is not None and (
            row["schema_version"] != _SCHEMA_VERSION or row["configuration"] != configuration
        ):
            raise QueueMismatch("SQLite namespace registry, settings, or schema do not match")
        connection.execute(
            "INSERT OR IGNORE INTO anyo_namespaces VALUES (?, ?, ?)",
            (self._namespace, _SCHEMA_VERSION, configuration),
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS anyo_jobs ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, "
            "id TEXT NOT NULL, hook_id TEXT NOT NULL, fingerprint TEXT NOT NULL, "
            "event TEXT NOT NULL, max_attempts INTEGER NOT NULL, retry_delay REAL NOT NULL, "
            "retry_interrupted INTEGER NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL, "
            "available_at REAL NOT NULL, owner TEXT, token TEXT, lease_until REAL, "
            "finished_at REAL, error TEXT, UNIQUE(namespace, id))"
        )
        # Additive schema-1 migration preserves existing queue namespace fingerprints.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(anyo_jobs)")}
        for name, kind in {
            "created_at": "REAL",
            "started_at": "REAL",
            "control": "TEXT",
            "control_requested_at": "REAL",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE anyo_jobs ADD COLUMN {name} {kind}")
        connection.execute("UPDATE anyo_jobs SET created_at=available_at WHERE created_at IS NULL")
        connection.execute(
            "UPDATE anyo_jobs SET token=lower(hex(randomblob(16))) "
            "WHERE status='queued' AND token IS NULL"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS anyo_workers (namespace TEXT NOT NULL, id TEXT NOT NULL, "
            "record TEXT NOT NULL, heartbeat_at REAL NOT NULL, command TEXT, "
            "PRIMARY KEY(namespace, id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS anyo_log_cursors (namespace TEXT PRIMARY KEY, "
            "cursor INTEGER NOT NULL DEFAULT 0, pruned_cursor INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO anyo_log_cursors(namespace) VALUES(?)", (self._namespace,)
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS anyo_logs (namespace TEXT NOT NULL, "
            "cursor INTEGER NOT NULL, "
            "received_at REAL NOT NULL, record TEXT NOT NULL, run_id TEXT, worker_id TEXT, "
            "source TEXT NOT NULL, level TEXT NOT NULL, PRIMARY KEY(namespace, cursor))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS anyo_jobs_eligible "
            "ON anyo_jobs(namespace, status, available_at, sequence)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS anyo_jobs_history ON anyo_jobs(namespace, finished_at)"
        )

    def _validate_job(self, job: Job) -> None:
        if not job.id or not job.hook_id or not job.fingerprint:
            raise ValueError("job identity and fingerprint must be nonempty")
        if type(job.max_attempts) is not int or job.max_attempts < 1:
            raise ValueError("job max_attempts must be a positive integer")
        if (
            isinstance(job.retry_delay, bool)
            or not isinstance(job.retry_delay, (int, float))
            or not math.isfinite(job.retry_delay)
            or job.retry_delay < 0
        ):
            raise ValueError("job retry_delay must be finite and nonnegative")
        if not isinstance(job.event, str):
            raise ValueError("job event must be a serialized string")
        if type(job.retry_interrupted) is not bool:
            raise ValueError("job retry_interrupted must be a boolean")

    def _admit(self, connection: sqlite3.Connection, jobs: Sequence[Job]) -> int:
        now = self._now(connection)
        self._maintain(connection, now)
        unique: dict[str, Job] = {}
        eligible: list[tuple[Job, bool]] = []
        for job in jobs:
            if self._registry is None or self._registry.get(job.hook_id) != job.fingerprint:
                raise QueueMismatch("job does not match a registered hook")
            if job.id in unique:
                if self._definition(unique[job.id]) != self._definition(job):
                    raise QueueMismatch("conflicting definitions for one job identity")
                continue
            unique[job.id] = job
            row = connection.execute(
                "SELECT * FROM anyo_jobs WHERE namespace=? AND id=?",
                (self._namespace, job.id),
            ).fetchone()
            if row is not None:
                if self._definition(self._job(row)) != self._definition(job):
                    raise QueueMismatch("stored job definition does not match redelivery")
                if (
                    row["status"] in ("queued", "running", "successful", "cancelled")
                    or row["control"]
                ):
                    continue
            eligible.append((job, row is None))
        if self._active_count(connection) + len(eligible) > self._settings.capacity:
            raise CapacityError("queue capacity is full")
        records = connection.execute(
            "SELECT COUNT(*) FROM anyo_jobs WHERE namespace=?", (self._namespace,)
        ).fetchone()[0]
        if records + sum(new for _, new in eligible) > self._settings.max_records:
            raise CapacityError("queue retained history is full")
        for job, new in eligible:
            if new:
                connection.execute(
                    "INSERT INTO anyo_jobs (namespace, id, hook_id, fingerprint, event, "
                    "max_attempts, retry_delay, retry_interrupted, status, attempt, "
                    "available_at, created_at, token) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                    (
                        self._namespace,
                        job.id,
                        job.hook_id,
                        job.fingerprint,
                        job.event,
                        job.max_attempts,
                        job.retry_delay,
                        int(job.retry_interrupted),
                        now,
                        now,
                        uuid.uuid4().hex,
                    ),
                )
            else:
                self._reset(connection, job.id, now)
        return len(eligible)

    def _maintain(self, connection: sqlite3.Connection, now: float) -> None:
        expired = connection.execute(
            "SELECT * FROM anyo_jobs WHERE namespace=? AND status='running' AND lease_until<=?",
            (self._namespace, now),
        ).fetchall()
        for row in expired:
            replay = (
                not row["control"]
                and row["retry_interrupted"]
                and row["attempt"] < row["max_attempts"]
            )
            connection.execute(
                "UPDATE anyo_jobs SET status=?, available_at=?, finished_at=?, owner=NULL, "
                "token=?, lease_until=NULL, error=? "
                "WHERE namespace=? AND id=?",
                (
                    "queued" if replay else "interrupted",
                    now + self._delay(row) if replay else now,
                    None if replay else now,
                    uuid.uuid4().hex if replay else None,
                    "anyo.cancel_unconfirmed" if row["control"] else "anyo.lease_expired",
                    self._namespace,
                    row["id"],
                ),
            )
        connection.execute(
            "DELETE FROM anyo_jobs WHERE namespace=? AND finished_at IS NOT NULL "
            "AND finished_at<=? AND status IN ('successful', 'failed', 'interrupted', 'cancelled')",
            (self._namespace, now - self._settings.retention_seconds),
        )

    def _active_count(self, connection: sqlite3.Connection) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM anyo_jobs WHERE namespace=? "
                "AND status IN ('queued', 'running')",
                (self._namespace,),
            ).fetchone()[0]
        )

    def _reset(self, connection: sqlite3.Connection, job_id: str, now: float) -> None:
        connection.execute(
            "UPDATE anyo_jobs SET status='queued', attempt=0, available_at=?, owner=NULL, "
            "token=?, lease_until=NULL, finished_at=NULL, error=NULL, started_at=NULL, "
            "control=NULL, control_requested_at=NULL WHERE namespace=? AND id=?",
            (now, uuid.uuid4().hex, self._namespace, job_id),
        )

    @staticmethod
    def _now(connection: sqlite3.Connection) -> float:
        return float(
            connection.execute("SELECT (julianday('now') - 2440587.5) * 86400.0").fetchone()[0]
        )

    @staticmethod
    def _delay(row: sqlite3.Row) -> float:
        delay = float(row["retry_delay"])
        exponent = int(row["attempt"]) - 1
        if delay == 0:
            return 0.0
        # Compare before exponentiation, including very small positive delays.
        if exponent >= math.log2(3600.0) - math.log2(delay):
            return 3600.0
        return min(3600.0, math.ldexp(delay, exponent))

    @staticmethod
    def _definition(job: Job) -> tuple[str, str, int, float, bool]:
        # A provider delivery may be retried with different transport metadata
        # or JSON key order. Its first accepted event remains the job's snapshot.
        return (
            job.hook_id,
            job.fingerprint,
            job.max_attempts,
            job.retry_delay,
            job.retry_interrupted,
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            row["id"],
            row["hook_id"],
            row["fingerprint"],
            row["event"],
            row["max_attempts"],
            row["retry_delay"],
            bool(row["retry_interrupted"]),
        )
