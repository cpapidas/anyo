# Build a protected operations UI

## For Humans

A developer running Anyo needs to see what is executing, inspect agent and application logs, and stop work that has become stuck. The implementation adds a protected browser UI for those operations while pipelines continue to be defined in Python. This task records the agreed scope, the delivered design, its operational limits, and verification evidence.

Status: done  
Last updated: 2026-10-11

## Objective and scope

Deliver an operations dashboard with environment-configured authentication, run inspection and retry, run cancellation, worker controls, and unified live and retained logs. Cover the default combined SQLite runtime and separate receiver/worker deployments using SQLite or Redis. Implementation was authorized on 2026-10-11 following review of this plan.

Related knowledge: [operations guide](../../knowledge/operations-ui.md), [product direction](../../knowledge/product-overview.md), [worker queues](../../knowledge/worker-queues.md), [webhook contract](../../knowledge/webhook-triggers.md), [OpenCode prompts](../../knowledge/opencode-prompts.md), and [native OmO prompts](../../knowledge/omo-prompts.md). The [accepted implementation design](design.md) resolves the choices left open when this task was proposed.

The implementation follows the [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), [packaging](../../../skills/anyo-packaging/SKILL.md), and [documentation](../../../skills/anyo-docs/SKILL.md) skills. Anyo remains one distributable Python library; the dashboard uses packaged HTML, CSS, and JavaScript with no separate frontend application distribution.

## Requested behavior

### Authentication

- Configure a single operator username and password through `ANYO_UI_USERNAME` and `ANYO_UI_PASSWORD` environment variables.
- Do not expose an operational UI when either credential is missing or empty. If the consumer explicitly enables the UI with invalid configuration, fail with an actionable error and no credential values.
- Protect the dashboard, its operation APIs, and live/history log endpoints with the same authentication boundary. Browser assets must not contain credentials.
- Document HTTPS setup for remote access. Preserve provider signature authentication for webhook routes; GitHub and Slack deliveries must not require the dashboard login.

### Runs and workers

- Show runs with status, hook identity, attempt number, timestamps available from the runtime, and errors. Provide status filtering and a details view with the run's logs.
- Preserve the distinction between pipeline completion and an agent-reported failure: a pipeline can currently return normally after receiving a failed prompt result. Do not silently redefine existing success semantics to simplify the UI.
- Provide explicit retry for eligible failed or interrupted runs. Make clear that retry repeats execution and may repeat external effects.
- Show identified worker processes, their observed state, last heartbeat, and active runs. Treat heartbeat freshness as liveness evidence, not proof that a pipeline is making progress.

| Action | Intended behavior |
| --- | --- |
| Cancel run | Request cancellation of one queued or running execution. Show the request as pending until the runtime confirms the outcome; leave unrelated runs executing. |
| Force stop run | Terminate a stuck execution through a process boundary that isolates it from unrelated runs. Clean up its owned harness and child processes within the documented containment limits. |
| Stop worker | Stop that worker from taking new jobs and allow its active runs to finish. Other workers continue processing the queue. |
| Force stop worker | Stop the selected worker's execution service and terminate its owned work. Identify affected active runs before the action and report their resulting state; preserve the combined receiver/UI. |

Cancellation and forced termination do not undo completed external actions. Define terminal status, retry eligibility, and deduplication after each operation; an operator cancellation must not be silently undone by automatic retry or lease recovery.

### Unified logs

- Provide one log view covering Anyo receiver/runtime logs, worker logs, consumer pipeline logging and standard output/error, and OpenCode/OmO activity and diagnostics exposed by their integrations.
- Support live updates and retained history with filters for run, worker, source, and severity where available. Preserve general application logs even when no run can be assigned.
- Correlate entries with namespace, worker, job/run, attempt, and prompt invocation where known. Do not assign concurrent runs' output to each other.
- Include agent text, progress, and tool activity exposed by each harness. Inventory raw process and tool output separately: an unavailable source must be identified, not presented as fully captured.
- Apply secret redaction before persistence or streaming; avoid displaying credentials in authentication errors and exception output. Document limits for arbitrary consumer text and split or encoded secrets. Keep reasoning opt-in and subject to what the harness exposes.
- Define bounded retention and storage, maximum entry sizes, ordering, and behavior under disconnects or slow clients. Display explicit gaps or truncation when output is lost. Existing historical full logs cannot be reconstructed from current queue records.

## Historical baseline

Before this task, durable queues exposed `inspect()` and `retry()` with safe job summaries. The HTTP application served provider webhooks without dashboard authentication or operation APIs. Complete logs and arbitrary pipeline results were not stored. The current [operations guide](../../knowledge/operations-ui.md) supersedes this baseline for UI, controls, and diagnostic history; pipeline return values remain outside the new log contract.

Synchronous callbacks previously had only thread execution, where a stuck thread could not be safely terminated independently and its lease heartbeat could continue. This task adds explicit process isolation while preserving thread mode as the default. Importable callback requirements, fresh-process state, custom harness serialization, and POSIX restrictions are documented rather than silently changing callback compatibility.

Earlier task cancellation was a worker failure/shutdown signal, not a per-run operator control. The implementation separates those paths, stores commands in shared storage, fences them to the current attempt or queued revision, and supervises isolated callbacks from outside their execution process. A browser acknowledgment means a request was stored; confirmed state comes from execution or explicit lease-recovery policy.

The original prompt observers forwarded limited progress; OpenCode raw process logs/tool bodies and OmO stderr were not retained. Opt-in operations capture now records the supported provider diagnostics independently of observers, with redaction and bounded buffering. The existing observer and authoritative result contracts remain intact; host logging levels and handlers are preserved.

## Resolved implementation choices

The choices below were open in the initial plan and were resolved during the authorized implementation. See the [accepted design](design.md) and [user guide](../../knowledge/operations-ui.md) for the full contracts.

| Area | Decision and constraint |
| --- | --- |
| UI delivery | Packaged HTML/CSS/JavaScript at `/ui/` on the existing ASGI listener; `ui=True` enables operations. Views refresh through two-second cursor polling. |
| Authentication | Environment credentials, bounded eight-hour process-local cookie sessions, constant-time comparison, login throttling, and same-origin CSRF checks. HTTPS/proxy configuration remains deployment-owned. |
| Control transport | An optional backend-neutral operations protocol implemented by SQLite/Redis, persistent worker commands, and explicit ownership. No arbitrary PID termination API. |
| Process isolation | POSIX spawn for importable top-level sync/async callbacks; an independent supervisor handles force stop and owned harness cleanup. Thread mode retains its limitations. Worker stop preserves the combined receiver/UI. |
| State and races | Pending commands remain distinguishable from confirmed outcomes. Attempt/queued-revision fencing prevents stale confirmations from affecting a newer attempt; cancellation is terminal without automatic replay. |
| Log transport and storage | Contextual bounded capture and local IPC feed shared storage. Retention defaults are 24 hours/10,000 entries/16 KiB per entry. Redaction precedes persistence; gaps, truncation, and unavailable sources are explicit. |
| Compatibility | Operations require durable storage. SQLite changes are additive, but all same-namespace processes must upgrade together because older code cannot interpret new outcomes/control revisions. Existing public imports and ordinary hook/prompt calls remain compatible. |

No external frontend framework, log service, or supervisor service was introduced. Execution owns its local process supervisor; the deployment still owns HTTPS, worker placement, and external-effect idempotency.

## Implementation outcome

The implementation delivers Runs, Run details, Workers, and Logs views in the existing Python distribution. Authentication protects operational pages/data and state-changing controls; provider webhook authentication remains independent. The dashboard presents pending controls, confirmed outcomes, unavailable workers, log loss, and thread-mode limitations explicitly.

The execution capability owns operation contracts, worker lifecycle, and isolated run supervision; SQLite/Redis adapters persist commands and diagnostics. The new observability capability captures contextual Python output and provider diagnostics without changing configured logger levels or existing handlers. OpenCode and OmO retain their observer/result behavior while exposing supported raw diagnostics and tool output to the opt-in collector. Unscoped records are never broadcast across multiple application collectors.

Packaged UI assets, the [runnable local example](../../../examples/operations_ui.py), the [operations guide](../../knowledge/operations-ui.md), and updated worker/harness documentation describe configuration, independent worker deployments, retention, HTTPS, callback restrictions, and the coordinated namespace upgrade. No model calls, package publication, or production deployment are required for this functionality.

## Completion criteria and verification

- [x] An installed package can start the documented protected UI. Missing/empty credentials never expose operations; invalid credentials cannot access pages, APIs, or log streams. Verify the chosen session/authentication and CSRF behavior, HTTPS deployment guidance, and absence of credential values in responses or logs.
- [x] Runs and details show the documented state, errors, attempts, and available timestamps. Filtering and retry work; pipeline outcome and prompt outcome remain distinguishable. Worker views show active work and explain stale or unavailable state.
- [x] A queued cancellation prevents execution. Running cancellation affects only the selected attempt, with visible acknowledgment/outcome. Deterministic tests cover cancellation racing with claims/completion, repeated commands, lost ownership, storage failure, and the selected terminal/retry/deduplication policy.
- [x] A controlled stuck synchronous run can be force-stopped in the supported isolated mode while a sibling run and the receiver/UI remain usable. Verify owned child-process cleanup and no pending tasks or leaked resources. Unsupported execution combinations produce an explicit limitation instead of a false success.
- [x] Graceful worker stop prevents new claims and drains active work. Force stop terminates the targeted worker's owned execution through the documented independent control path, identifies affected runs, and does not terminate unrelated workers. A lost/unreachable worker is reported honestly.
- [x] Controls and logs work with separate receiver/worker processes using shared local SQLite and with workers accessing shared Redis. Verify requests reach the current owner, survive the documented restart scenarios, and respect namespace isolation. Use real SQLite subprocesses and explicitly selected real Redis checks; local fakes alone do not establish distributed behavior.
- [x] Deterministic fixtures produce identifiable pipeline logging, stdout/stderr, receiver/runtime entries, OpenCode progress/diagnostics, and OmO progress/diagnostics. Each documented source appears live and in history with correct attribution; unavailable sources and gaps are visible. Include concurrent runs and multiple prompt calls within one run.
- [x] Verify secret redaction before storage and streaming, rendered-log escaping, bounded retention, reconnect behavior, slow clients, oversized entries, and collector/storage failures. General logs remain accessible when their application is known. History remains available across restarts for the configured retention period.
- [x] Existing hook, Slack responder, prompt result, observer, and queue behavior checks pass. Default tests require no credentials, paid models, or external services; use events/barriers and bounded process fixtures for concurrency. Record any separately selected live harness verification accurately.
- [x] Run relevant tests, formatting, lint, and type checks. Build from the source archive, inspect the wheel, and verify the UI assets and supported runtime flows from a clean installation outside the checkout. Provide setup examples and update the linked knowledge with the delivered contract and actual verification results.

## Verification results

The checks below were completed on 2026-10-11, including a full copied consumer suite against the clean installed wheel outside the checkout.

| Check | Observed result |
| --- | --- |
| Source test suite | 581 passed, 35 deselected. Existing hook, responder, agent, and queue contracts remain covered. |
| Static checks | Ruff lint and formatting passed for 83 files; mypy passed for 40 source files; JavaScript syntax passed `node --check`. |
| Real Redis | Explicitly selected checks: 26 passed, 2 skipped because disposable restart checks require separate opt-in, 33 deselected. Additional isolated Redis 6.2/7.4 AOF restart and cross-process checks were run separately; these do not imply multi-host network deployment certification. |
| Browser verification | Login, signed webhook success/failure/stuck states, live logs, pending cooperative cancellation followed by force stop, retry/new-attempt controls, retained logs after restart, and worker views exercised. |
| Distribution contents | Wheel runtime files matched all 47 source files byte-for-byte; required assets and `py.typed` were present. Metadata checks passed; the 112-entry source archive contained no runtime secrets. |
| Clean base installation | Outside the checkout, a new virtual environment with no optional Redis, HTTPX2, or pytest installed exercised real HTTP authentication/CSRF, installed assets, signed admission, a successful isolated run, stale-token rejection, forced cancellation, captured stdout/logging, worker views, and logout. `pip check` passed. |
| Clean installed consumer suite | 581 passed, 35 deselected in 22.42 seconds. The explicitly selected installed-wheel Redis 7.4 checks also passed: 26 passed, 2 disposable-restart checks skipped, 33 deselected. |

The clean environment used Python 3.12.14, Starlette 1.7.0, Uvicorn 0.54.0, HTTPX2 2.13.1, and redis-py 8.1.0 where the optional extras were installed. The base-only smoke ran before those extras were added. `pip check` and `twine check` passed. The wheel and source archive were built locally as `anyo-0.1.0.dev0`; neither was published. Other Python/OS combinations were not exercised for this task. Temporary verification services/processes were stopped after the checks.

Focused evidence lives in [UI tests](../../../tests/test_ui.py), [operation storage tests](../../../tests/test_operations_storage.py), [worker/process tests](../../../tests/test_operations_workers.py), [observability tests](../../../tests/test_observability.py), and the OpenCode/OmO operation fixtures under their integration test directories. These checks use controlled harness fixtures; no paid model call was made for this task.

## Scope limits

This is an operations UI. Visual pipeline creation, editing Python workflows in the browser, prompt optimization, automatic remote webhook setup, account management, SSO, and multiple operator roles remain outside this task. Retained logs and run inspection do not imply a persistent store for arbitrary pipeline return values. Thread mode cannot promise targeted forced termination; process ownership does not contain deliberately detached external processes. Unknown/transformed secrets, unavailable harness output, direct native writes, and ambiguous unscoped multi-application logs remain explicit collection limits. Implementation authorization did not request production deployment, package publication, or paid model calls.

## Sources

- Project conversation, 2026-10-11 (no share link supplied): requested UI brainstorming based on the existing docs; discussed Runs, Run details, and Retry; requested protection through environment-configured username/password, cancellation of running or stuck work, and OpenCode/OmO plus general logs; then requested creation of this task.
- Same conversation: proposed `ANYO_UI_USERNAME` / `ANYO_UI_PASSWORD`, distinct run and worker controls, process isolation for targeted force stop, unified live/history logs, and secret redaction. The user requested a task for that scope and subsequently authorized implementation; the [accepted design](design.md) records the resolved engineering choices.
- Linked project knowledge and initial read-only inspection established the historical baseline. The implementation, tests, browser checks, and distribution verification above establish the delivered behavior and its stated limits.
