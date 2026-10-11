# Execute webhook pipelines with SQLite and Redis workers

## For Humans

A developer should be able to receive a webhook, save its work, and let workers run the pipeline without installing another service for the first deployment. As that deployment grows, the developer should be able to switch to Redis and run workers on other machines while keeping the same pipeline code. This task records the delivered queue backends and workers, the original design proposals, and their verification after the completed OpenCode integration.

Status: done  
Created: 2026-10-04  
Last updated: 2026-10-04  
Scope: interchangeable SQLite and Redis queue backends, worker execution, durable delivery state, consumer configuration, and verification.

## Objective and authorization

Deliver SQLite and Redis implementations behind one execution contract so users can change the backend through configuration without rewriting hooks or pipelines. The user first requested task creation, then explicitly authorized implementation on 2026-10-04. The requirements below distinguish the requested outcome from the original engineering proposals; the [execution contract](../../knowledge/worker-queues.md) records the implementation choices and verification status.

Dependencies: the completed [webhook task](../webhook-triggering-system/plan.md), [Slack task](../slack-hooks/plan.md), and [OpenCode task](../opencode-prompt-execution/plan.md). Related contracts: [webhook triggering](../../knowledge/webhook-triggers.md), [Slack hooks](../../knowledge/slack-hooks.md), [OpenCode invocation](../../knowledge/opencode-prompts.md), and [product direction](../../knowledge/product-overview.md).

Apply the repository's [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), [packaging](../../../skills/anyo-packaging/SKILL.md), and [documentation](../../../skills/anyo-docs/SKILL.md) guidance.

## Confirmed requirements and original baseline

- Provide both SQLite and Redis implementations, with an easy configuration change between them.
- Move background pipeline handling behind a worker pull boundary after webhook admission. Long pipeline and OpenCode execution must not delay the webhook acknowledgment.
- Provide a useful path without an external queue service. SQLite supplies that path; Redis supports deployments whose workers need shared storage across machines.
- Preserve Anyo's Python library model and code-authored pipelines, including synchronous and asynchronous callbacks.

At task creation, the runtime ran pipelines in the background through an in-process FIFO queue, with four concurrent pipelines and capacity for 100 running or queued jobs by default. It had process-local deduplication, explicit failed-hook redelivery, no automatic retries, and no durable storage. This task introduced persistence and independent workers, beyond changing synchronous functions to `async def`.

The original hooks had registration-order IDs such as `hook-1`; queued jobs contained live `Hook` and event objects. Admission was synchronous, and prompt clients belonged to the application's ASGI lifespan. These boundaries required changes while preserving provider and callback behavior.

## Original consumer experience proposal

The baseline proposal is SQLite by default, with the receiver and local worker tasks started by the existing `anyo.start()` entry point. Separate receiver and worker processes are optional. SQLite uses the standard-library driver; Redis adds an optional `anyo[redis]` extra. These defaults, parameter names, and URL syntax were proposals at task creation. The planning sections below preserve that history; the outcome section and linked execution contract describe the delivered APIs.

Illustrative configuration after registering the same hooks and harness:

```python
import os
import anyo

# Register hooks and the optional OpenCode harness here, in either role.

if __name__ == "__main__":
    anyo.start(
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/queue.sqlite3"),
        queue_namespace="review-app",
        role=os.environ.get("ANYO_ROLE", "all"),
    )
```

The example reads environment variables in consumer code; it does not assume automatic library environment discovery. A Redis deployment would set the URL to `redis://localhost:6379/0` or an authenticated TLS URL. Never put real credentials in documentation or default logs.

| Role | Behavior |
| --- | --- |
| `all` | Start HTTP reception and local worker tasks in one application process. This preserves a single-command entry point and does not automatically spawn worker containers. |
| `receiver` | Authenticate, route, run immediate responders, and admit jobs; do not execute background pipelines or start OpenCode for those pipelines. |
| `worker` | Load the application's registrations and harness configuration, claim jobs, and execute pipelines; do not bind an HTTP port or require webhook signing secrets merely to consume verified jobs. |

Workers use the same application code, namespace, and compatible registration definitions as receivers. Configuration selects the backend; neither hooks nor prompt calls receive Redis or SQLite client objects. Keep an explicit in-memory mode for tests and consumers who intentionally want the existing disposable behavior, if needed for compatibility.

Finalize the public startup signatures, ASGI integration, worker-only lifecycle API, default database location, and namespace rules before coding their public surface. Relative paths must resolve predictably, directories must be writable, and importing Anyo must not create files or open connections.

## Shared execution contract

The proposed flow is `authenticate and route → atomically persist eligible jobs → acknowledge`, followed independently by `claim → execute → record outcome`. Slack's immediate response decision remains before background admission.

### Admission, identities, and serialization

- Introduce stable hook identities independent of registration order, process memory addresses, or secrets. Define when an identity can be derived safely and when the consumer must supply an explicit key. Validate duplicates and incompatible registry definitions before serving or consuming work.
- Preserve registered functions, bound methods, and callable objects through a worker-loaded registry. Do not pickle callables, clients, credentials, or arbitrary Python objects into jobs. Explain the migration for closures and other registrations that cannot be identified automatically, including registrations made in a consumer's main block.
- Define a versioned job envelope containing the namespace, stable hook identity, provider/delivery identity, event type and data, schema/registration compatibility information, and attempt metadata. Keep signing secrets and harness credentials in process configuration.
- Preserve `HookEvent` and `SlackEvent` behavior, including Slack's typed actions and installations, optional context, and a private payload copy for each invocation. Define an explicit event codec extension point for custom providers; unsupported values fail before any part of a fan-out is admitted. Queue data must never name arbitrary classes to import or instantiate.
- Atomically admit all newly eligible matched hooks or none, including shared deduplication and capacity accounting. A duplicate must not suppress a different hook, application, or provider. A partially successful delivery must retain success for completed hooks when other hooks are explicitly redelivered.
- Await storage admission within the request's existing deadline. Acknowledge only after the backend confirms the write under its documented persistence policy. Storage failure, full capacity, or unresolved admission produces a retryable response; a write that committed before a connection loss must be safe to submit again with the same identity.

### Claims, recovery, and limits

- Define atomic claims with an owner, attempt token, lease expiry, and heartbeat renewal. Completion, failure, and renewal must check the current token so a stale worker cannot overwrite a newer attempt. Run consumer code outside database transactions and never hold a storage lock during an OpenCode call.
- Persist queued, running, successful, failed, and interrupted/uncertain outcomes. Queued work survives application restart while backend data is retained. Expired running leases must become recoverable rather than disappearing or blocking capacity forever.
- Separate retryable pipeline failures from uncertain execution after a crash. The proposed compatibility default is no automatic rerun of failed or interrupted attempts; explicit redelivery can retry them. Add an explicit bounded retry policy for hooks whose effects permit replay, including delayed retry/backoff and a terminal outcome when attempts are exhausted. Lease recovery must not bypass that policy.
- Define at-least-once attempts only where replay is enabled and storage survives; do not promise exactly-once effects. A heartbeat cannot prevent an old worker from producing external effects after its lease expires. Provide stable job/delivery identifiers for consumer idempotency and stop new work when backend ownership cannot be maintained.
- Keep concurrency local to each worker process; define capacity and retained delivery history across the whole queue namespace. Preserve the current defaults where meaningful, document any changed scope, bound persisted payloads and history, and never prune jobs awaiting execution or retry. Use restart-safe stored timestamps and backend-authoritative time where possible; process-local monotonic timestamps cannot be persisted as shared deadlines.
- Specify FIFO eligibility and retry ordering without promising completion order across concurrent workers. Expose enough job/attempt state and safe logs to distinguish pending, running, retrying, failed, and interrupted work; no dashboard is required.

### Lifecycle and existing integrations

- Preserve sync pipelines in bounded threads and async pipelines on the worker's event loop. Database operations must not block that loop or Slack's response deadlines. Reuse the application context binding that makes module-level prompt calls resolve the correct client.
- Each worker owns its prompt client and, in the normal OpenCode mode, its local OpenCode process. A worker image needs the consumer code, prompts, credentials, and prepared workspace; a receiver's local path does not make those files available remotely. Keep standalone prompt clients usable outside workers.
- Preserve `PromptResult` semantics: returning a failure object does not itself fail a pipeline; consumers use `raise_for_failure()` or their own policy. Do not blindly replay `anyo.outcome_unknown` or timed-out agent work that may already have produced effects. An opted-in general retry policy must still distinguish these outcomes; raw pipeline crashes remain potentially uncertain.
- Keep Slack responders in the HTTP process with their current deadline, conflict handling, `dispatch=False`, and reply replay behavior. A successful responder followed by failed queue admission must still be replayable on redelivery. Shared job deduplication does not automatically coordinate responder execution; initially document response caching as process-local and do not claim exactly-once responders across receiver replicas or restarts.
- Split startup and shutdown ownership by role. Receivers stop admission and finish immediate responses; workers stop claiming and finish owned active attempts before closing prompt clients and storage. Persist unclaimed backlog for remaining/restarted workers. Explicitly document the change from draining the entire process-local queue, bounded host shutdown behavior, cancellation, and the inability to safely kill an arbitrary sync callback.

## Backend implementation proposals

| Backend | Implementation and deployment requirements |
| --- | --- |
| SQLite | Use `sqlite3`, a local database file, schema versioning, short transactions, atomic claims, bounded lock waits, and polling with bounded idle delay. Serialize connection access appropriately and move blocking storage work off the event loop. Support multiple processes on the same host; document a persistent Docker volume and reject or clearly exclude network-filesystem sharing. Test both process restart and contention. |
| Redis | Use the async `redis-py` client through an optional extra. Evaluate Streams consumer groups with acknowledged completion and pending-entry recovery, plus atomic scripts/transactions for fan-out, deduplication, leases, retries, and accounting. Do not use Pub/Sub or destructive dequeue alone for durable jobs. Prevent trimming or eviction of unfinished jobs and make client disconnection/reconnection bounded. |

SQLite WAL permits readers alongside a writer but still serializes writes and requires same-host access. Verify the actual linked SQLite runtime, including the documented WAL-reset fix, before choosing concurrent WAL as the supported default; the Python version alone does not establish the SQLite version. Record the tested version policy and any fallback journal mode. [SQLite WAL documentation](https://www.sqlite.org/wal.html).

Redis persistence depends on server configuration: snapshots, AOF, fsync policy, and failover can have different loss windows. Publish the tested configuration and limits rather than equating a successful Redis write with unconditional disk durability. Backend selection must fail clearly when dependencies, permissions, supported versions, or connectivity are missing; never silently fall back to memory or a different backend. [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

Select supported Redis server/client versions from the commands actually used and verify their lower bounds. Streams acknowledgment and reclamation are building blocks, not the complete Anyo retry contract. Initial Redis scope can be one configured standalone endpoint; Cluster and Sentinel discovery require explicit additional support, not an implied guarantee. [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/), [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/), [async client lifecycle](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).

## Backend switching and compatibility

Switching means choosing another backend with the same hook code, serialized event contract, and documented execution policy. It does not automatically transfer queue contents, retained deduplication history, or active attempts.

Provide a concrete cutover guide: pause ingress or arrange provider redelivery, drain the old backend, inspect unresolved attempts, stop its consumers, change the connection configuration consistently, then resume workers and reception. Explain that delivery history starts separately on the new backend and previously completed deliveries may run again after cutover; consumer idempotency remains necessary. Do not delete the old queue as part of configuration switching. Automatic live migration, dual writes, and cross-backend failover are outside this task.

Document changes to durable hook identity, JSON-compatible event data, writable storage, retained state across sequential starts, shared capacity, and shutdown behavior. Keep existing public imports and ordinary registration/prompt call patterns; do not silently restrict supported callable forms. Unknown hooks, unsupported envelope/schema versions, or incompatible deployed registrations must leave jobs diagnosable and must never execute another hook by accident.

## Component ownership and implementation sequence

| Existing capability | Work |
| --- | --- |
| `src/anyo/execution.py` | Backend-neutral queue contract, job state, worker execution, retry policy, and lifecycle. Promote to `execution/` only when implementation responsibilities justify it, preserving public imports and the separate immediate-response dispatcher. |
| `src/anyo/application.py` and `src/anyo/hooks.py` | Stable registration identity, configuration, awaited admission, role-aware startup, event serialization contracts, and provider-independent routing. |
| Concrete storage adapters | Follow the execution-owned contract and repository integration boundaries. Resolve exact placement during implementation; shared execution must not import a Redis SDK or concrete adapter. |
| `src/anyo/agents.py` and OpenCode integration | Reuse prompt ownership and context binding in worker lifecycles; preserve current invocation and uncertainty semantics. |
| Package root, metadata, examples, and Docker documentation | Inert default composition, optional Redis dependencies, consumer entry points, persistent storage, and separate role examples. |

1. Finalize the proposed API/defaults, stable identity rules, envelope/codec, state transitions, retry/uncertainty policy, role lifecycle, and compatibility changes in a focused execution contract under knowledge. Resolve these engineering choices within this task rather than treating this planning document as delivered behavior.
2. Introduce the shared storage boundary and worker runner, preserving existing callback and responder behavior. Add contract tests against a deterministic backend and real temporary SQLite databases.
3. Implement SQLite admission, claims, recovery, history, schema lifecycle, and the default combined flow; verify independent local worker processes and persistence across restarts.
4. Implement Redis against the same contract, including atomic admission, fencing, reconnect/recovery, optional installation, and real-server verification.
5. Wire receiver/worker roles, OpenCode lifecycle, examples, and the documented backend switch procedure. Update current knowledge and README claims only when behavior is implemented and verified.
6. Verify source and installed-package behavior, record exact commands/results and remaining limits, and mark this task done only after both backends meet the criteria below.

## Completion criteria and verification

- [x] A fresh base installation can run SQLite without Redis, an added Python database package, or network services. A missing SQLite capability or unwritable path produces an actionable startup error.
- [x] The same registered sync and async pipelines execute through SQLite and Redis by changing configuration only; a missing Redis extra produces a precise installation hint. Importing Anyo has no storage/network/process side effects.
- [x] Combined, receiver-only, worker-only, and ASGI hosting paths have documented lifecycle behavior. Worker-only startup needs no HTTP port/signing secret; receiver-only operation does not start background prompt execution.
- [x] Shared contract, backend, and application tests together cover all-or-none fan-out, capacity/history limits, duplicate deliveries, partial success/redelivery, delayed retries, namespace isolation, and provider/event round trips including Slack and a consumer-defined provider.
- [x] Stable identity tests cover reordered registration, multiple processes, renamed/missing/incompatible hooks, explicit identities for non-importable callables, and unsupported schema versions. No job is rebound to the wrong callable.
- [x] Crash tests cover admission committed before lost acknowledgment, receiver restart with pending jobs, worker death before/after an external effect, expired leases, heartbeat renewal, stale completion, cancellation, and retry exhaustion. Ambiguous outcomes follow the documented replay policy without claiming exactly-once effects.
- [x] Actual SQLite subprocess workers contend safely and recover using a retained database. Real Redis checks cover multiple consumers, atomic admission, pending recovery, connection interruption, retained-state restart, and the documented persistence profile. Fakes alone are not Redis verification.
- [x] Slack tests preserve its total deadline, immediate replies, conflict detection, response replay after admission failure, and dispatch suppression. Backend delays cannot block unrelated event-loop work; distributed responder limitations are explicit.
- [x] Worker/application tests with a fake prompt backend verify ownership, context binding, concurrent invocations, and failure cleanup. Runner tests preserve uncertain OpenCode outcomes, and existing OpenCode adapter tests verify transport and cleanup with local HTTP/SSE fixtures. Paid model calls remain separately selected and unnecessary for queue verification.
- [x] Default tests stay deterministic and require no external services. Real Redis tests use an explicit selection mechanism and bounded local fixtures; absent Redis is recorded as unverified rather than passed. Use events/barriers and controlled clocks instead of timing sleeps for concurrency assertions.
- [x] Run relevant tests, Ruff, mypy, source-to-wheel build, metadata checks, and clean installed-wheel verification outside the checkout. Verify both base-only installation and Redis/OpenCode extras, package resources, supported version bounds, and no leaked tasks/connections/processes.
- [x] Examples and setup documentation show local SQLite, persistent Docker storage, Redis, separate roles, inspection of failed/interrupted jobs, shutdown, and a safe drained backend cutover. A consumer can follow these examples without rewriting pipeline bodies.

## Outcome and verification

Implemented on 2026-10-04. The package-root `anyo.start()` now defaults to SQLite at `sqlite:///.anyo/queue.sqlite3`; the optional Redis adapter is selected with `queue_url="redis://localhost:6379/0"` after installing the Redis extra. The same registered pipelines support combined, receiver-only, and worker-only roles. `Application()` retains its original memory default for advanced composition, and existing execution imports remain available after promotion to the `execution/` package.

The shared contract includes atomic admission, stable hook identities, registered JSON event codecs, persisted delivery history, fenced leases, heartbeat renewal, bounded opt-in retries, inspection, and explicit retry. Slack immediate responders stay in the receiver; worker-owned OpenCode clients preserve prompt context and uncertain-outcome handling. Sync pipelines use a dedicated thread pool so they cannot starve SQLite storage operations. Normal shutdown finishes owned active attempts and leaves backlog for other or restarted workers.

SQLite uses the standard library, short transactions, DELETE journaling, and FULL synchronization. This supports the tested local runtimes without relying on concurrent WAL fixes. Redis uses atomic Lua over hashes and sorted sets; explicit leases and state transitions provide the shared contract without Streams consumer groups. The adapter prevalidates key types and command/key permissions before mutations, keeps operations bounded, and reconnects on later admission attempts after an outage. Server persistence remains operator configuration.

A namespace pins the complete hook registry and shared limits. Changing the backend URL or namespace does not copy jobs or deduplication history; the [worker guide](../../knowledge/worker-queues.md) documents draining and cutover. It also covers persistent Docker storage, custom event codecs, failed/interrupted job inspection, and deployment limits. [The queue example](../../../examples/queued_webhook.py) and [OpenCode example](../../../examples/opencode_review.py) accept the same backend and role configuration.

Verification completed against the final runtime source:

| Check | Result |
| --- | --- |
| `.venv/bin/ruff check src tests examples` and `.venv/bin/ruff format --check src tests examples` | Passed; all 60 Python files formatted. |
| `.venv/bin/python -m mypy` | Passed for 32 source files. |
| `.venv/bin/python -m pytest -q` | 502 passed, 16 live cases deselected. Includes real SQLite subprocess contention, forced worker death before/after an external effect, retained-state recovery, and graceful signal shutdown. |
| Shared queue contract | The same four contract scenarios passed against SQLite and real Redis, including sync/async callbacks, retries, payload preservation, capacity, and deduplication. |
| Real Redis matrix | All 11 adapter live cases passed for each combination of Redis 6.2.24 / 7.4.11 and redis-py 5.0.8 / 8.1.0. Includes retained-state server restart, connection interruption, lost acknowledgment, ACL changes, and lease fencing. |
| Source-to-wheel build and metadata | Built the source archive and then the wheel from that archive; `twine check` passed for both. Required adapters, Lua module, typing marker, and prompt resource are present. |
| Clean base-only wheel | Installed outside the checkout with Starlette 1.7.0 and Uvicorn 0.53.0. Redis and HTTPX2 were absent during SQLite worker and actual HTTP startup/shutdown smoke checks; `pip check` passed. |
| Full tests against installed wheel | After adding test dependencies, 502 passed and 16 live cases were deselected outside the checkout, without `PYTHONPATH`. Runtime files match the final source byte for byte. |
| Installed Redis/OpenCode extras | Imports and `pip check` passed. Explicit Redis live and shared-contract selection: 14 passed, one restart case intentionally skipped, four SQLite cases deselected. The source Redis matrix above separately verified the restart case. |
| Docker | Rebuilt `anyo-workers:verification` for native ARM64. UID 10001 admitted a signed webhook to SQLite, ran it in a separate worker lifecycle, and preserved deduplication after reopening. The existing real-OpenCode smoke passed with six local model-fixture calls, crash recovery, plugin failure, and independent runtimes. |

The host and clean environment used Python 3.12.14 and SQLite 3.53.1. The rebuilt container used SQLite 3.40.1 and OpenCode 1.18.34. Redis restart checks used AOF with `appendfsync everysec` and `maxmemory-policy noeviction`; they verify retained state after an orderly restart, not survival of every storage crash or failover. The guide separately shows the stronger `appendfsync always` option. Redis tests are opt-in through `ANYO_TEST_REDIS_URL` and `-m live`; the restart case additionally requires an explicitly selected disposable container through `ANYO_TEST_REDIS_CONTAINER`. Both disposable task Redis containers were removed afterward.

Verified local artifacts are the [wheel](../../../dist/sqlite-redis-workers-final/20261004T082917Z/anyo-0.1.0.dev0-py3-none-any.whl) and [source archive](../../../dist/sqlite-redis-workers-final/20261004T082917Z/anyo-0.1.0.dev0.tar.gz). Their SHA-256 values are `36946078d5a6478615b7fea1a6cbed209bb85b6aa6128c68008af7b0db5c7006` and `5994df0b0042f5997b8556c6d95c214f08d941fc47f6d54ad88b182c6afa9696`, respectively. These artifacts precede the documentation completion-status update; all runtime source matches. No package was published, paid model call made, or new AMD64 image verification claimed by this task.

## Scope limits and sources

This task does not add a general workflow DAG engine, scheduling product, dashboard, automatic checkout, remote webhook provisioning, another broker, live queue migration, automatic backend failover, or a package release. Basic job outcome inspection and explicit redelivery/retry are part of operating the queue; broader orchestration remains separate.

- Project conversation, 2026-10-04 (no share link supplied): requested workers after OpenCode and an easy path without external dependencies; then requested a task for both SQLite and Redis with easy switching, before implementation. The user then explicitly authorized implementation in the same conversation.
- Existing source and linked project contracts, inspected on 2026-10-04: establish current callback, response, identity, delivery, packaging, and prompt ownership behavior.
- [Python SQLite documentation](https://docs.python.org/3.12/library/sqlite3.html) and the primary SQLite/Redis references linked above, checked on 2026-10-04: storage capabilities and limits, not approval of the proposed Anyo API.
