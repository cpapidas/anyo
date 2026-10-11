# Operations UI implementation design

## For Humans

Anyo's dashboard runs alongside its webhook receiver and communicates with workers through their shared storage. This design keeps browser access protected and moves force-stoppable user code into separate processes. It records implementation decisions for the requested task; verification is recorded in the [plan](plan.md).

Status: accepted  
Last updated: 2026-10-11

## Delivery and configuration

Use packaged HTML, CSS, and JavaScript served by the existing Starlette application at `/ui/`. No frontend build tool, external CDN, extra application, or external hosting service is needed. Enable HTTP access with `ui=True`; this also enables operations collection for that receiver or combined process. Separate workers use `operations=True`. Both use the same queue URL and namespace. The default remains disabled, and `memory://` explicitly rejects operations.

Add `execution_mode="process"` for spawn-based isolated execution of importable top-level sync/async pipeline functions. Existing thread execution remains the default and retains its callback compatibility. Process mode transfers trusted event/configuration data through owned local IPC, never through the browser. Consumers guard application startup with `if __name__ == "__main__"`. Unsupported callbacks fail explicitly rather than falling back to thread execution. Process containment requires POSIX.

## Authentication and browser boundary

Read `ANYO_UI_USERNAME` and `ANYO_UI_PASSWORD` only when the UI is enabled. Both must be nonblank. Use a bounded, expiring server-side session with an unpredictable cookie, HttpOnly and SameSite=Strict attributes, and Secure on HTTPS. Login compares credentials in constant time, limits repeated failures, and never logs supplied credentials. Mutating API requests require the session's independent CSRF token and same-origin checks. Logout revokes the session. A process restart requires logging in again.

Serve scripts and styles from the package and render all log data as text. Add a restrictive Content Security Policy, no-store for operational responses, and frame protection. Webhooks retain their existing provider signature checks independently of the UI. Remote use requires HTTPS termination with correctly configured trusted proxy behavior in the ASGI host.

## Runtime controls

Keep operation records in the selected SQLite/Redis namespace. Extend the concrete backends through an optional operations protocol so consumer queue implementations and legacy operation without the UI remain compatible. Track worker identity, state, heartbeat, active runs, and execution mode. Claims and operation requests are fenced to the current execution, and repeated requests are idempotent.

Queued cancellation prevents a subsequent claim. Running cancellation requests cooperative cleanup; force stop terminates only an isolated run's owned execution. A separate supervising loop remains responsive when user code hangs. A worker stop disables claims and drains active work; worker force stop terminates its owned isolated runs while preserving the combined receiver/UI. The standalone worker command exits when execution has stopped. Thread mode cannot promise forced termination and must reject unsupported force operations.

Cancelled jobs are terminal and do not automatically retry or restart through webhook redelivery. Explicit operator retry remains available. Completion racing with a command is resolved atomically; the UI distinguishes a recorded request from confirmed completion. Neither cancellation nor process termination rolls back external effects. Stale heartbeat means unavailable state, not proof of termination.

## Logging

Collect runtime/Python logging, contextual stdout/stderr, structured prompt progress and outcome, and harness diagnostics through an opt-in logging session. Preserve existing handlers and logging levels. Store bounded redacted records before returning them to browser polling requests; polling from a cursor provides live updates without holding execution open for a browser.

Records include available worker, run, attempt, and prompt IDs. Unattributed process logs remain general entries when their application collector is known. With several active collectors, ambiguous unscoped logs are omitted instead of broadcast across namespaces; explicit application binding selects the correct collector and clears foreign run context. Separate processes send correlated records through owned IPC; remote workers write to the shared backend. Log collection uses bounded buffers and records gaps rather than blocking a pipeline indefinitely. Native raw diagnostics require redaction before persistence; no universal capture of unavailable harness/tool output or model reasoning is claimed.

Log retention is independent of queue job retention and explicitly configurable through operations settings. Authentication credentials, registered provider secrets, and harness model credentials are redacted; arbitrary encoded or unknown secrets remain an application-data limitation. Existing full historical logs cannot be recovered retroactively.

## Compatibility and deployment

SQLite upgrades existing queues with additive operation fields/tables, retaining the queue schema identifier. All processes sharing a namespace must be stopped and upgraded together because earlier versions do not understand `cancelled` or operation revisions. Redis operations use the same namespace as their job records. Both backends fence browser commands to the selected attempt or queued revision so a delayed confirmation cannot affect later work.

The [operations guide](../../knowledge/operations-ui.md) defines startup, retention defaults, session behavior, process-mode callback restrictions, supported output sources, and deployment limits. Tests use controlled local harnesses and explicitly selected real Redis checks; this task does not establish new live model or detached-process containment guarantees.

## Sources

- [Implementation task](plan.md) and project conversation, 2026-10-11: requested behavior and implementation authorization.
- Existing worker and harness contracts linked from the task: compatibility and ownership constraints.
