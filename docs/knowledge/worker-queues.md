# Worker queues

## For Humans

Anyo accepts a webhook before a pipeline finishes, so accepted work needs storage that survives application restarts. SQLite provides that storage on one machine, and Redis lets receivers and workers share it across machines. This guide explains configuration, delivery guarantees, recovery, and deployment with the same pipeline code.

Status: draft  
Last updated: 2026-10-11  
Scope: implemented contract for the [SQLite and Redis worker task](../tasks/sqlite-redis-workers/plan.md); completed verification and tested versions are recorded there.

## Start with SQLite, switch through configuration

A base installation includes SQLite through Python's standard-library `sqlite3` module. There is no additional database package or service to run. From a local checkout:

```sh
python -m pip install /path/to/anyo
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python examples/queued_webhook.py
```

The [runnable example](../../examples/queued_webhook.py) registers a stable hook and reads queue settings from consumer-owned environment variables:

```python
import os
import anyo

# Register the same hooks, stable hook_id values, and optional harness in every role.
anyo.start(
    queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/queue.sqlite3"),
    queue_namespace="github-reviews",
    role=os.environ.get("ANYO_ROLE", "all"),
)
```

`anyo.start()` itself defaults to `sqlite:///.anyo/queue.sqlite3`, namespace `default`, and role `all`. The example selects `github-reviews` to distinguish its application. Construction, import, and registration create no files or connections. Parent directories are created during startup; a missing SQLite capability or unwritable location fails startup clearly.

Install Redis support and change the URL when using a Redis service:

```sh
python -m pip install '/path/to/anyo[redis]'
export ANYO_QUEUE_URL='redis://localhost:6379/0'
python examples/queued_webhook.py
```

The optional extra declares `redis-py>=5.0.8`; the adapter requires a standalone Redis server at least 6.2. `rediss://user:password@host:6379/0` selects TLS with certificate and hostname checks. Keep actual credentials in runtime configuration. URL query parameters and fragments are rejected; configure operation timeouts through Anyo. Cluster and Sentinel discovery are outside this implementation. Switching URLs does not copy the old queue: follow the cutover procedure below.

| Role | Owns |
| --- | --- |
| `all` | HTTP receiver and local worker tasks in one process. This is the single-command default. |
| `receiver` | Authentication, matching, immediate responders, and durable admission. No background pipelines or pipeline prompt client. |
| `worker` | Queue polling and pipeline execution, with its own prompt client. No listening socket or webhook signing-secret requirement. |

For separate processes, run the same example twice with the same URL, namespace, and registrations:

```sh
ANYO_ROLE=receiver python examples/queued_webhook.py
# In another terminal; provide the same queue and repository configuration.
ANYO_ROLE=worker python examples/queued_webhook.py
```

The example reads environment variables in consumer code; Anyo does not automatically discover `ANYO_QUEUE_URL` or `ANYO_ROLE`. SQLite processes must share the same local file on one host. Redis workers can run on other machines, provided they have the consumer code, prompt files, credentials, and workspaces they need. A receiver's filesystem paths are not transferred with a job.

## Identity and namespace compatibility

Give durable hooks an explicit `hook_id`, and set `hook_version` when their behavior changes:

```python
anyo.add_hook(
    provider="github", action="pr_created", organization="your-org", repository="your-repo",
    pipeline=report_pull_request, hook_id="report-pull-request", hook_version="1",
)
```

Top-level plain functions can derive an identity from their module, qualified name, provider, action, and normalized configuration. Closures, lambdas, bound methods, and callable instances remain supported with an explicit ID. Automatic identities can change when code moves or is executed as `__main__` instead of imported; explicit IDs make deployments predictable. Workers execute the registered callable, never a callable deserialized or imported from job data. Code inside a consumer's main block must run in every process role. `Hook.id` remains a process-local registration identifier; durable storage uses the resolved stable identity.

A namespace pins the complete pipeline registry fingerprints, schema, capacity, maximum retained records, and retention period. Registration order may differ; missing hooks or changes to identities, versions, routing configuration, retry policies, or shared limits raise `QueueMismatch`. Create a new namespace and perform a drained cutover for these changes. Secrets are excluded from fingerprints. Keep the same consumer code version across workers: the registry does not hash arbitrary function bodies. Concurrency, polling, operation timeout, and lease duration are local process settings.

## Admission, execution, and recovery

The receiver authenticates and matches hooks, makes any immediate response decision, persists all newly eligible jobs atomically, and then acknowledges. Capacity rejection cannot partially admit a fan-out. Acceptance means the backend confirmed its write under the configured persistence policy; it does not mean execution succeeded. If a connection fails after commit, acknowledgment is uncertain and the provider may redeliver safely using the same identity.

Delivery identity includes namespace, stable hook fingerprint, provider, and provider delivery ID. Queued, running, and successful records suppress duplicates. Explicit redelivery of failed/interrupted records resets their attempt budget without rerunning successful sibling hooks. Within retained history, the first admitted serialized event remains the job's input, including on redelivery: later payload changes or retry metadata do not replace it. After retention expiry, the same identity may be admitted as new work.

Workers claim eligible jobs in queue order, execute outside storage transactions, and renew leases while working. Concurrency and delayed retries mean completion order is unspecified. Sync callbacks run in a bounded, dedicated worker thread pool, separate from SQLite storage operations; async callbacks and returned awaitables run on the worker's event loop. Return values are discarded, and each invocation gets an independent decoded event/payload.

A completion or renewal must match the current unexpired claim token. A worker claim/heartbeat storage failure or lost ownership stops further local admission/claims; owned callbacks are awaited and stale completions cannot overwrite a newer attempt. The token protects queue state, not arbitrary external effects. An old worker or remote service may still produce effects after its lease expires. Restart an affected worker after correcting its storage/ownership failure. An admission-only storage outage returns a retryable response; the receiver remains available for later redelivery and reconnection. Neither case changes to another backend automatically.

The default policy makes one attempt and does not automatically repeat failed or interrupted work. Opt into bounded retries only for replay-safe pipelines:

```python
from anyo import RetryPolicy

# Pass this policy to add_hook(..., retry=...).
policy = RetryPolicy(max_attempts=3, delay=2, retry_interrupted=False)
```

`max_attempts` includes the first attempt. Failure delay doubles after each attempt, capped at one hour. Replaying an expired running lease also requires `retry_interrupted=True`; otherwise it becomes an inspectable `interrupted` record. Cancellation and process-control failures stop the local runner and mark owned attempts interrupted where storage permits. Explicit retry/redelivery remains an operator decision and can repeat external effects.

An explicitly raised OpenCode `PromptFailed` with `anyo.outcome_unknown` or `anyo.timeout` is interrupted and never automatically replayed, even under the general retry policy. A process crash can leave effects equally uncertain; keep interrupted retries disabled unless the whole pipeline is safe to repeat. Returning or logging a failed `PromptResult` still counts as successful pipeline completion; use `result.raise_for_failure()` to signal failure. At-least-once attempts apply where replay is enabled and storage survives; exactly-once external effects are not promised.

## Limits, inspection, and lifecycle

The optional [operations UI](operations-ui.md) adds protected inspection and controls, retained logs, and POSIX process isolation. Use `ui=True` on a receiver or combined application and `operations=True` on separate workers. `execution_mode="process"` enables targeted force stop for importable top-level callbacks; the default thread mode preserves existing callable support. Upgrade every process sharing a namespace together before enabling these features.

| Option | Default | Scope |
| --- | --- | --- |
| `workers` | `4` | Pipeline concurrency per worker process; separate responder concurrency per receiver. |
| `capacity` | `100` | Queued plus running records across the namespace, including delayed retry. |
| `max_records` | `10000` | All retained job records across the namespace. |
| `retention_seconds` | `3600` | Terminal history expiry measured from completion. Active jobs do not expire for retention. |
| `lease_seconds` | `30` | Claim duration; renewals occur every third of this interval. |
| `poll_interval` | `0.1` | Maximum normal idle polling delay in seconds. |
| `operation_timeout` | `2` | Storage-operation budget in seconds. |

Choose a lease comfortably longer than storage-operation latency and timeouts. Backend wall time is used for stored deadlines; do not put process-local monotonic times into persisted state. Encoded event envelopes are limited to one MiB and finite JSON values with bounded nesting. Full retained history rejects new delivery IDs until terminal records expire, rather than silently deleting active work.

An async host can own workers explicitly after registering the application's hooks:

```python
async def consume(application):
    async with application.worker(queue_namespace="github-reviews") as runner:
        for job in await runner.inspect(limit=100):
            print(job.id, job.hook_id, job.status, job.attempt, job.error)
        # After reviewing a known failed/interrupted attempt:
        # await runner.retry(job_id)
        await runner.wait_idle()
```

`anyo.worker(...)` supplies the same context manager for the default application. `inspect` reports IDs, status, attempt, timestamps, current owner, control request, and safe error codes without returning event payloads. Outcomes are `queued`, `running`, `successful`, `failed`, `interrupted`, and operator-`cancelled`; delayed retries are queued with a later availability time. `retry(id)` explicitly resets a failed, interrupted, or cancelled job and enforces shared capacity. Cancelled work suppresses webhook redelivery until an explicit retry or history expiry; cancellation with lost ownership is reported as `interrupted` / `anyo.cancel_unconfirmed` and cannot automatically replay. The `attempt_token` is an opaque revision for the current queued/running state, allowing browser controls to reject stale confirmations after a claim or retry. Queue problems use `CapacityError`, `QueueUnavailable`, or `QueueMismatch` from `anyo.execution`. Ordinary pipeline failures store a safe exception type; uncertain OpenCode outcomes retain the known failure code. Default runtime logs omit exception messages and payloads; opt-in operations logging captures additional consumer and harness output according to the [coverage contract](operations-ui.md).

`wait_idle()` scans retained records and waits until the namespace has no queued/running work, including delayed retries. A concurrent producer can add work immediately afterward. It propagates local fatal worker errors. It is different from closing the runner: normal durable shutdown stops new claims, finishes owned active attempts, closes their prompt clients and queue connection, and preserves unclaimed backlog. SIGTERM/SIGINT in worker-only `start()` request this shutdown. A hung sync callback can still delay it, so consumer external calls need deadlines. Forced termination leaves recovery to leases and the configured replay policy.

`memory://` retains the original process-local FIFO dispatcher, restart-cleared history, and full draining of accepted work on shutdown. It supports only role `all`, has no durable inspection/retry interface, and keeps explicit redelivery without automatic retries. This compatibility mode does not share capacity or deduplication between processes.

## Advanced application composition and ASGI

Unlike the package-root convenience API, `Application()` retains its historical memory default. Compose concrete backends explicitly:

```python
from anyo.application import Application
from anyo.integrations.github import GitHubProvider
from anyo.integrations.redis import RedisQueue
from anyo.integrations.sqlite import SqliteQueue

application = Application(
    providers=[GitHubProvider()],
    queue_backends={"sqlite": SqliteQueue, "redis": RedisQueue, "rediss": RedisQueue},
    queue_url="sqlite:///.anyo/queue.sqlite3",
)
# Register application.add_hook(...) before entering its lifecycle.
app = application.asgi(role="receiver", queue_namespace="github-reviews")
```

Serve the resulting app with an asyncio ASGI host and lifespan enabled. The host owns HTTP addresses and signals; use `application.worker(...)` or `application.start(role="worker")` for a process with no HTTP. While the ASGI lifespan is active, `app.state.dispatcher` exposes the durable runner for local inspection. Concrete adapters implement the execution-owned `QueueBackend` contract; core execution imports neither a Redis SDK nor concrete storage integrations. Missing optional dependencies or invalid configuration fail explicitly, with no fallback to another backend.

## Event codecs for custom providers

Built-in `HookEvent`, `SlackEvent`, and `BetterStackEvent` preserve all their fields, including typed Slack actions/installations and Better Stack native context. Custom subclasses need a trusted codec in both receiver and worker. A codec's stable name includes its schema version. Its encoder must produce finite JSON-compatible values and its decoder must validate its own fields; stored data cannot name arbitrary Python classes to import.

For a custom event whose extra context lives in its JSON payload, this complete codec preserves the base fields and exact subclass:

```python
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from anyo.hooks import HookEvent

@dataclass(frozen=True)
class TicketEvent(HookEvent):
    pass  # Custom provider data stays in payload for this example.

class TicketCodec:
    name = "consumer.ticket.v1"
    event_type = TicketEvent

    def encode(self, event: HookEvent) -> Mapping[str, object]:
        if type(event) is not TicketEvent:
            raise ValueError("Expected TicketEvent")
        return asdict(event)

    def decode(self, data: Mapping[str, object]) -> HookEvent:
        required = {"provider", "action", "delivery_id", "organization", "repository"}
        if set(data) != required | {"payload", "pull_request_number", "head_sha"}:
            raise ValueError("Unexpected ticket fields")
        if any(not isinstance(data[key], str) for key in required):
            raise ValueError("Ticket identifiers must be strings")
        if not all(data[key] for key in ("provider", "action", "delivery_id")):
            raise ValueError("Ticket identity cannot be empty")
        if not isinstance(data["payload"], dict):
            raise ValueError("Ticket payload must be an object")
        number, sha = data["pull_request_number"], data["head_sha"]
        if number is not None and type(number) is not int:
            raise ValueError("Invalid PR number")
        if sha is not None and not isinstance(sha, str):
            raise ValueError("Invalid head SHA")
        return TicketEvent(**data)

# Include your adapter in providers, and register the codec:
# Application(providers=[ticket_provider], event_codecs=[TicketCodec()], ...)
```

Alternatively, expose `event_codec` on the provider adapter; `Application` discovers that explicit structural contract. Register a codec only once. Serialization validates the whole matched group before any job is admitted. Unsupported classes, non-finite numbers, arbitrary objects, or unsupported schema versions fail rather than invoking pickle or dropping fields.

## SQLite and Docker storage

SQLite uses local files, short `BEGIN IMMEDIATE` transactions, `journal_mode=DELETE`, and `synchronous=FULL`. It deliberately does not enable WAL, avoiding dependence on the linked SQLite version's concurrent WAL fixes. Operations run outside the event loop and connection access is serialized. Multiple local processes are supported; network-filesystem sharing is outside the supported deployment.

`sqlite:///queue.sqlite3` is relative to the process working directory; `sqlite:////queue/queue.sqlite3` is absolute. Containers sharing local SQLite must mount the same host-local storage. Persist a writable data directory separately from read-only application code, and permit the image's UID/GID 10001 to write it.

The existing [Dockerfile](../../Dockerfile) includes OpenCode but not the Redis extra. For SQLite, create a queue directory and persistent volume in an extended image:

```dockerfile
FROM anyo-opencode
USER root
RUN mkdir -p /queue && chown 10001:10001 /queue
USER anyo
```

Build this as `anyo-queue`, then run the example with a named volume:

```sh
docker run --rm --name anyo-worker-app \
  --env-file /absolute/path/to/runtime.env \
  -e ANYO_QUEUE_URL=sqlite:////queue/queue.sqlite3 -e ANYO_HOST=0.0.0.0 \
  -p 8000:8000 -v anyo-queue:/queue -v "$PWD/examples:/app:ro" \
  anyo-queue python /app/queued_webhook.py
```

A new named volume inherits the image directory's ownership. An existing volume or host bind mount must already be writable by UID 10001. Give separate receiver/worker containers the same URL, namespace, registration environment, and host-local volume; omit the published HTTP port for worker-only containers. Queue payloads can contain private provider data, so treat backups and volume access as application data access.

For Redis, build the local wheel first and extend the image with its extra, without assuming this development package exists on a package index. Create a separate build directory containing the selected `anyo-*.whl` from `dist/` and the following `Dockerfile`; run `docker build -t anyo-redis /path/to/that-directory`. The repository's `.dockerignore` excludes `dist`, so this separate context is required for the example:

```dockerfile
FROM anyo-opencode
USER root
COPY anyo-*.whl /tmp/anyo-wheels/
RUN for wheel in /tmp/anyo-wheels/anyo-*.whl; do \
      python -m pip install --no-cache-dir "${wheel}[redis]"; \
    done && rm -rf /tmp/anyo-wheels
USER anyo
```

Configure a standalone Redis instance with persistent storage and an explicit policy, for example:

```text
appendonly yes
appendfsync always
maxmemory-policy noeviction
```

The adapter uses atomic Lua scripts over hashes and sorted sets, not Pub/Sub or destructive dequeue. AOF with `always` requests synchronization on each write; `everysec`, snapshots, and failover have different possible loss windows. `noeviction` makes memory pressure reject work instead of silently removing queue keys. Anyo does not configure the server, and successful Redis acknowledgment is not an unconditional guarantee against all server/storage/failover loss. Final verified server versions and persistence checks are recorded in the task, not inferred from this example policy. [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

## Cut over without changing pipelines

1. Stop ingress or arrange provider redelivery, then drain the old queue with its existing workers.
2. Inspect failed/interrupted attempts and decide whether to retry them; confirm no active attempts remain.
3. Stop the old consumers. Keep their database/history intact for inspection.
4. Update the backend URL consistently for every receiver/worker, preserving hook definitions. Use a new namespace if registry or shared settings change.
5. Start workers and reception on the new configuration, then resume ingress.

A new backend or namespace has separate delivery history. Previously successful deliveries may run again if redelivered after cutover, so consumer idempotency remains necessary. Automatic history transfer, live migration, dual writes, and cross-backend failover are outside this implementation.

## Sources

- User implementation authorization, 2026-10-04 (conversation, no share link supplied), and the linked task.
- [Webhook contract](webhook-triggers.md), [Slack contract](slack-hooks.md), and [OpenCode contract](opencode-prompts.md).
- [Python SQLite](https://docs.python.org/3.12/library/sqlite3.html), [SQLite WAL limitations and fix](https://www.sqlite.org/wal.html), [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/), and [redis-py async lifecycle](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html), checked during task research on 2026-10-04.
