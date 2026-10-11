# Protected operations UI

## For Humans

An operator needs to see which pipelines are running, read their output, and stop work that has become stuck. Anyo's optional browser UI provides these controls while pipeline definitions remain in Python. This guide explains setup, execution modes, log coverage, and the limits of remote control.

Status: accepted  
Last updated: 2026-10-11  
Scope: implemented UI and operations contract; final verification is recorded in the [operations UI task](../tasks/protected-operations-ui/plan.md).

## Start a local dashboard

The [runnable example](../../examples/operations_ui.py) registers a GitHub hook whose pipeline prints ten local progress steps. It performs no model calls or external pipeline actions. With Python 3.12 or later on Linux or macOS, install from the repository root and supply your own credentials through the environment:

```sh
python -m pip install .
export ANYO_GITHUB_ORGANIZATION='demo-owner'
export ANYO_GITHUB_REPOSITORY='demo-repository'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-webhook-secret'
export ANYO_UI_USERNAME='operator'
export ANYO_UI_PASSWORD='replace-with-a-separate-long-random-password'
python examples/operations_ui.py
```

Open `http://127.0.0.1:8000/ui/` and sign in. The example uses SQLite at `.anyo/operations.sqlite3`, namespace `operations-demo`, and process execution. The listener accepts signed webhooks at `/webhooks/github`; UI credentials do not replace GitHub or Slack signature authentication.

To try the UI without configuring GitHub, run this signed local fixture in a second terminal with the same environment values. It creates one ten-second run; repeat with a new delivery ID to create another:

```sh
python - <<'PY'
import hashlib
import hmac
import json
import os
import uuid
from urllib.request import Request, urlopen

body = json.dumps({
    "action": "opened",
    "number": 1,
    "repository": {
        "owner": {"login": os.environ["ANYO_GITHUB_ORGANIZATION"]},
        "name": os.environ["ANYO_GITHUB_REPOSITORY"],
    },
    "pull_request": {"head": {"sha": "a" * 40}},
}).encode()
signature = hmac.new(
    os.environ["ANYO_GITHUB_WEBHOOK_SECRET"].encode(), body, hashlib.sha256
).hexdigest()
request = Request("http://127.0.0.1:8000/webhooks/github", body, headers={
    "Content-Type": "application/json",
    "X-GitHub-Event": "pull_request",
    "X-GitHub-Delivery": str(uuid.uuid4()),
    "X-Hub-Signature-256": "sha256=" + signature,
})
with urlopen(request, timeout=5) as response:
    print(response.status, response.read().decode())
PY
```

For real deliveries, configure the repository webhook using the [GitHub setup guide](../../examples/README.md#connect-github). Use a separate secret and password, and keep both outside source control.

## Enable operations in your application

After registering hooks, the combined runtime can enable the dashboard with:

```python
anyo.start(ui=True, execution_mode="process")
```

`ui=False` and `operations=False` are the defaults. `ui=True` enables operations and requires nonempty `ANYO_UI_USERNAME` and `ANYO_UI_PASSWORD`; invalid credentials stop startup. `operations=True` enables controls and log collection without serving a dashboard. Operations require SQLite or Redis; `memory://` is unsupported. `Application.asgi(...)` accepts the same `ui`, `operations`, and `execution_mode` settings. An advanced `Application` must be composed with its durable backend as shown in the [worker guide](worker-queues.md#advanced-application-composition-and-asgi).

Configure retention through `OperationsSettings`:

```python
from anyo.execution import OperationsSettings

operations = OperationsSettings(
    log_max_entries=10000,
    log_retention_seconds=86400,
    max_log_bytes=16384,
    worker_retention_seconds=86400,
)
anyo.start(ui=True, operations=operations, execution_mode="process")
```

These defaults retain up to 10,000 log entries for 24 hours, with at most 16 KiB per entry. Completed queue jobs have a separate default retention of one hour through `retention_seconds`; logs can therefore outlive the Runs entry they describe. Set the same operations limits on every process sharing a namespace.

## Separate receiver and workers

Run the same example and hook registrations in each role:

```sh
ANYO_ROLE=receiver python examples/operations_ui.py
# In another terminal with the same queue/repository configuration:
ANYO_ROLE=worker python examples/operations_ui.py
```

The receiver serves `/ui/` and accepts webhooks. Workers use `operations=True` or the same `OperationsSettings`, and do not serve HTTP or need UI/webhook credentials. Passing `ui=True` to `role="worker"` is rejected. The example enables the correct settings for each role; its `ANYO_ROLE`, `ANYO_QUEUE_URL`, `ANYO_QUEUE_NAMESPACE`, `ANYO_EXECUTION_MODE`, `ANYO_HOST`, and `ANYO_PORT` variables are consumer configuration, not automatic library discovery.

For SQLite, all processes must use the same absolute local database path on one host. For remote workers, install `anyo[redis]` from the checkout, supply a shared Redis service, and set the same `ANYO_QUEUE_URL` and namespace in each deployment. Each worker needs its own installed application code, prompt files, workspaces, and model credentials. Controls travel through the shared backend to the current owner; no worker HTTP port is required. A backend URL change does not copy jobs, logs, or history.

## Read runs and control execution

Runs can be searched by run/hook ID and filtered by status. Run details show available timestamps, attempts, ownership, errors, control requests, and correlated logs. Workers show execution mode, active runs, and their latest heartbeat. A fresh heartbeat indicates that the worker is reachable; it does not prove that user code is progressing.

| Action | Behavior |
| --- | --- |
| Retry run | Explicitly requeues an eligible failed, interrupted, or cancelled run with its original stored event. Previous external effects may repeat. |
| Cancel run | Cancels queued work immediately or requests cooperative cancellation of the active attempt. A running request stays pending until its outcome is confirmed. |
| Force stop run | In process mode, terminates the selected execution and its owned processes. Other runs continue. |
| Stop worker | Stops new claims and lets active work finish. Other workers continue. |
| Force stop worker | In process mode, stops new claims and terminates that worker's owned executions. The confirmation identifies affected active runs. |

Controls do not undo external actions. Confirmed cancellation records `cancelled`; it is not automatically retried or revived by webhook redelivery while retained. An explicit Retry is required. A control request with lost ownership can become `interrupted` with `anyo.cancel_unconfirmed`, which does not establish that external effects stopped. Once retained delivery history expires, the delivery can be admitted as new work.

Stopping a worker controls its execution service. In combined mode, the receiver and dashboard remain available, and can accept work for other or subsequently restarted workers. A standalone `anyo.start(role="worker")` exits after its worker stops. No UI action accepts an arbitrary operating-system PID or stops unrelated workers.

The dashboard refreshes active views every two seconds. Logs use cursor polling, and the visible log panel retains up to 1,000 entries. Pausing live updates does not stop server collection; resuming continues from the cursor. Retention loss, capture gaps, and truncation are shown rather than treated as a complete history. An unreachable worker or unavailable backend is reported without claiming a control succeeded.

## Choose an execution mode

`execution_mode="thread"` preserves the existing execution model: plain functions run in worker threads, and async functions run on the event loop. Async cancellation is cooperative. A synchronous function must return before its cancellation can finish; a stuck Python thread cannot be independently killed. The UI does not offer Force stop for a thread worker, and the HTTP API rejects unsupported force-stop requests. Direct backend requests can remain pending and do not establish that execution stopped.

`execution_mode="process"` starts a fresh POSIX process for each attempt. An independent parent control boundary can stop that process even when the callback blocks its own event loop. It also cleans registered owned harness process groups and ordinary child processes. Processes deliberately detached outside this ownership boundary are not an OS containment guarantee.

Process mode requires an importable top-level synchronous or asynchronous function and a guarded entry point:

```python
def pipeline(event):
    # Initialize per-attempt resources here.
    ...

def main():
    # Register pipeline and configure the harness before startup.
    anyo.start(ui=True, execution_mode="process")

if __name__ == "__main__":
    main()
```

Closures, lambdas, bound methods, callable instances, and interactive/stdin-defined callbacks require thread mode. Fresh spawned interpreters do not inherit modified parent globals, open clients, locks, or logging configuration. Configure child resources in importable code or inside the callback. Built-in harness configuration can cross this local boundary; custom harness factories must be serializable. The queue still stores verified event data, not executable callback objects: only the worker's trusted registered callback and configuration enter its own local child process. Remote workers load their own code.

If the OS refuses to signal an owned process group, Anyo still reaps the pipeline process and reports `interrupted` with `anyo.process_cleanup_unconfirmed`. It does not claim a confirmed force stop.

## Log coverage and handling

Collection starts only when operations are enabled. It adds a Python logging handler and temporarily wraps Python stdout/stderr, retaining the host's existing handlers and levels. Records disabled by logger levels, and loggers that do not propagate to the root handler, are not captured through that handler. The consumer decides its logging policy. Direct file-descriptor writes, unrelated native subprocess output, and detached processes require their own integration; the dashboard does not claim to intercept all machine output.

| Source | Captured operations data |
| --- | --- |
| Anyo and consumer logging | Emitted records, including formatted exception details, with available run/attempt/worker context. General records remain visible without a run ID. |
| Python stdout/stderr | Line-buffered writes through the active Python streams. Split writes are joined before redaction; unterminated lines flush at graceful session close. |
| Prompt calls | Provider progress and prompt outcomes, even without `on_event`. Prompt IDs distinguish multiple calls within a run. Reasoning events remain opt-in through `include_reasoning=True`. |
| Managed OpenCode | Owned server stdout/stderr and structured tool state/input/output exposed by the adapter. A shared server's raw lines have no reliable run/prompt identity and remain general logs. |
| External OpenCode server | Structured invocation activity only; Anyo cannot capture that server's raw process streams. |
| Native OmO | Owned stderr, supported progress, tool execution records, and available failure diagnostics. Raw JSON-RPC stdout is interpreted as protocol, not indiscriminately copied as text. |

Prompt failures and pipeline status remain distinct. A failed `PromptResult` appears as a prompt outcome log, but a pipeline that returns normally still succeeds; call `result.raise_for_failure()` when that failure should fail the pipeline. Pipeline return values are not automatically stored as arbitrary results.

Known environment/configured credentials are redacted before entries enter persistence or the UI. This covers UI and webhook credentials, registered model keys, and owned server passwords; credential-like assignments and authentication headers receive additional filtering. Redaction is not a guarantee against unknown, encoded, transformed, or deliberately split secrets across independent records. Agent output can contain sensitive workspace information. Existing host console handlers/output retain the host's policy; operations redaction does not rewrite another handler's destination.

Run context follows Anyo's tasks and synchronous worker threads. Consumer-created threads need explicitly copied context to retain attribution; with one collector, uncorrelated entries stay general. When several applications collect logs in one process, output without a collector context is omitted because its namespace is ambiguous; it is never broadcast across their stores. Advanced hosts using `OperationsLogSession` can select a collector with `with session.bind():` and add run context with `log_scope(...)`.

Oversized entries, lines, and full collector buffers produce truncation/gap diagnostics. Collection is bounded and independent of browser speed. Storage failures are retried while possible; a permanently unavailable backend or forced termination can lose buffered entries, and a failed store cannot retain its own final loss message. Existing queue history cannot reconstruct logs produced before collection was enabled.

## Authentication and remote hosting

The dashboard supports one environment-configured operator account. Login creates an eight-hour, process-local session with an HttpOnly, SameSite=Strict cookie; an HTTPS request also sets Secure. Restarting the receiver invalidates its sessions. Login attempts are bounded per receiver process, and state-changing API calls require a session CSRF token and same-origin checks. Dashboard data, operations, and log endpoints require authentication; the login page and static assets contain no credentials. Multiple receiver processes require routing a browser session consistently to the receiver that created it.

Use HTTPS for remote access. Configure TLS on an ASGI host or a reverse proxy, preserve the public host, and configure trusted proxy headers so the ASGI request reports the public HTTPS scheme. Trust only your actual proxy addresses; otherwise cookie security and origin checks cannot rely on those headers. The convenience startup does not provision certificates or a reverse proxy. Keep `/ui/` on the same origin as its APIs and assets. Provider webhooks continue using their own signatures and do not send the UI cookie or CSRF token.

Changing environment credentials takes effect when the receiver restarts. There is no account database, SSO, or operator role system in this UI.

## Upgrade an existing queue

Operations add SQLite fields/tables and Redis records while retaining the existing queue schema identifier. The new `cancelled` outcome and control metadata are not understood by older workers. Stop and upgrade every receiver and worker sharing a namespace before using the new controls; a rolling mixture with older code is unsupported.

Drain active work, inspect uncertain attempts, and stop all participating processes. Back up the database or Redis data using the deployment's normal procedure, deploy the same upgraded application/package everywhere, and restart with matching registrations and operations settings. SQLite adds the required fields when opening the existing local database. If registry fingerprints or shared queue settings change as well, follow the [namespace/backend cutover procedure](worker-queues.md#cut-over-without-changing-pipelines). Keep the old data until inspection confirms the migration; do not run an older worker against records created by the new controls.

## Sources

- Project conversation, 2026-10-11 (no share link supplied): requested a protected UI with environment credentials, run and worker controls, OpenCode/OmO/general logs, and then authorized implementation.
- [Operations UI task](../tasks/protected-operations-ui/plan.md), [worker contract](worker-queues.md), [webhook contract](webhook-triggers.md), [OpenCode guide](opencode-prompts.md), and [native OmO guide](omo-prompts.md).
- Implemented application, execution, storage, UI, and observability contracts; controlled fixture verification is recorded in the task rather than presented as live model validation.
