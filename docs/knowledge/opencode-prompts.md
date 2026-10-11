# OpenCode prompt execution

## For Humans

A pipeline can ask OpenCode to work on a prompt file and use its answer in Python. Anyo owns the local process, reports progress separately, and validates a success or failure envelope around the user's requested JSON data. This guide describes the implemented API and its same-container deployment.

Status: accepted  
Last updated: 2026-10-11  
Scope: the OpenCode integration in Anyo 0.1.0.dev0, pinned to OpenCode 1.18.34.

## Configure and invoke

Install from the checkout with `python -m pip install '/path/to/anyo[opencode]'`. The optional extra supplies `httpx2>=2.13.1`; the executable is separate. Use the [Dockerfile](../../Dockerfile) for a bundled executable, or install OpenCode **1.18.34** on Linux/macOS. The adapter rejects other versions before submitting work.

```python
import os
from pathlib import Path

import anyo

handle = anyo.harness(
    provider="opencode",
    plugins=[],
    models=[{"model": os.environ["ANYO_MODEL"], "key": os.environ["MODEL_API_KEY"]}],
)


# In an existing synchronous hook:
def review_pull_request(event):
    result = anyo.inject_prompt(
        "review_pr.md",
        workspace=Path("/workspace"),
        context={"pr": event.pull_request_number, "head_sha": event.head_sha},
        timeout=300,
        on_event=lambda progress: print(progress.kind, progress.message),
    )
    if result.success is not None:
        print(result.success.data)
    else:
        print(result.failure.code, result.failure.message, result.failure.data)
        result.raise_for_failure()
```

Register the hook and call `anyo.start()` as in [the runnable example](../../examples/opencode_review.py). Configuration is inert: import, registration, and client construction do not start OpenCode or install plugins. Each worker process owns its prompt client during its execution lifespan; hooks inherit the correct application's client, including synchronous hooks dispatched to threads. Receiver-only processes do not create the pipeline prompt client. Durable shutdown finishes owned active hooks before closing OpenCode and preserves unclaimed jobs; memory mode still drains its local backlog. Each application has independent configuration and state.

Outside hooks, explicitly own a client:

```python
with anyo.PromptClient(harness=handle) as client:
    result = client.inject_prompt("review_pr.md", workspace="/workspace")
```

For asynchronous code, use `async with anyo.PromptClient(harness=handle) as client` and `await client.ainject_prompt(...)`. Async hooks use `await anyo.ainject_prompt(...)`. The async observer must be an `async def` callable; the synchronous observer is an ordinary callable. A synchronous prompt call on an event-loop thread raises a usage error. Clients are single-use context managers; async clients belong to their entering event loop, while sync clients own a loop thread.

For an independent application, explicitly wire the adapter:

```python
from anyo.application import Application
from anyo.integrations.github import GitHubProvider
from anyo.integrations.opencode import opencode_harness

application = Application(providers=[GitHubProvider()], harnesses={"opencode": opencode_harness})
handle = application.harness(
    provider="opencode", models=[{"model": "provider/model", "key": "..."}]
)
```

The root API wires OpenCode by default. `opencode_harness(...)` is also an inert standalone builder, without application registration. An application permits one harness registration; `application.reset_harness()` permits replacement while stopped. Registration/reset during lifespan is rejected. The separate [worker queue contract](worker-queues.md) now supplies SQLite/Redis execution without changing prompt call signatures; the completed queue task records worker lifecycle and container verification.

## Model, plugin, and runtime settings

Models accept `model`, `key`, and optional `base_url`. The first is the default; `inject_prompt(..., model="provider/another-model")` selects another registered entry. The identifier must name an OpenCode-supported model. Models of one provider must share credentials and base URL. There is no automatic model fallback, interactive login, OAuth, or custom model catalog API. Auxiliary model calls use the first configured model too.

Plugins are ordered OpenCode package specifications (prefer pinned versions) or absolute `file:` URLs. Code settings override ambient OpenCode config for owned processes. Anyo isolates XDG state and OpenCode's home lookup, disables default/Claude/project plugin discovery, supplies credentials in the child's private environment, and verifies effective configuration. Plugin initialization happens before model submission. Broken plugins return `anyo.configuration_failed`; plugin-specific external tools must be added to the consumer image. First initialization may download plugin support packages.

To choose native OmO instead, use `provider="omo"` with the [native adapter](omo-prompts.md). The [minimal review example](../../examples/README.md#choose-opencode-or-native-omo) demonstrates both executables in Docker with MiniMax M2.7. The former OmO plugin example was removed at the user's request; its [historical task](../tasks/omo-minimax-example/plan.md) retains the earlier verification. Other OpenCode plugins remain supported, and may have their own configuration discovery and external state beyond OpenCode's isolation.

The harness also accepts these keyword options:

| Option | Default | Behavior |
| --- | --- | --- |
| `executable` | `"opencode"` | Binary path/name, executed without a shell. |
| `state_directory` | System temp directory | Parent for a private directory removed at client shutdown. |
| `max_concurrency` | `4` | Active invocation limit; overflow returns `anyo.capacity_exceeded`. |
| `startup_timeout` | `30` seconds | Wait for the owned server's listening announcement. |
| `control_timeout` | `5` seconds | Absolute deadline per HTTP control request. Increase for slow plugin installs. |
| `shutdown_timeout` | `10` seconds | Grace period before killing the owned process group. |
| `poll_interval` | `0.5` seconds | Interval between stored-message/status checks. |
| `permissions` | Deny all, allow `read`, `glob`, `grep` | Mapping of tool names/patterns to `allow`, `deny`, or `ask`; must contain `"*"`. |

Anyo always allows the reserved `StructuredOutput` tool, required to return results. Explicitly denying it is a configuration error. Broader tool access is a deliberate code choice, for example `permissions={"*": "deny", "read": "allow", "bash": "allow"}`. Permission or question requests requiring a person fail with `anyo.interaction_required` and abort the invocation. Tool permissions and sessions do not isolate files: use separate workspaces/containers for independent mutable work. Plugins execute with the container user's permissions.

Advanced borrowed-server mode accepts `server_url`, `server_password`, and optional `server_username` (default `opencode`). Anyo checks version and compatible effective configuration; it never reconfigures or stops that server. Use a trusted private connection or HTTPS for a remote address. An unconfirmed remote abort cannot guarantee remote tool termination. Worker processes can run their own local OpenCode child without borrowed-server mode.

## Prompt and result contract

`inject_prompt` and `ainject_prompt` accept a UTF-8 file path, JSON-compatible `context`, existing `workspace` directory, positive finite `timeout` (default 300 seconds), optional registered `model`, `on_event`, and `include_reasoning=False`. Relative paths resolve against the caller's current directory; Anyo never changes process-wide working directory. Prompt plus labeled JSON context is limited to 1 MiB and is read once. Missing files, invalid JSON context, invalid configuration, unknown models, and lifecycle misuse raise Python exceptions before model submission.

Anyo injects its installed, versioned [instruction](../../src/anyo/_prompt_envelope.txt) through the system field and supplies a JSON schema. OpenCode must return:

```json
{"type":"success","data":{"comments":[]},"failure_code":null,"failure_message":null}
```

Or, for an agent-reported failure:

```json
{"type":"failure","data":null,"failure_code":"missing_pr_context","failure_message":"The PR diff is unavailable."}
```

`result.success` or `result.failure` is populated, never both. Success has `.data`; failure has `.data`, `.code`, `.message`, and `.origin` (`agent` or `runtime`). Result metadata includes `.run_id`, `.session_id`, and `.message_id` where available. Records are frozen; nested `.data` is a copied JSON tree owned by that result. Types are available in `anyo.agents` and `JSONValue` in `anyo.prompts`.

Data preserves the user's requested object, array, scalar, or null. Anyo adds no `comments` key and does not validate a user-specific inner schema. When the user requests no JSON, the injected instruction asks for null data; that semantic condition and actual task completion depend on the model, not on outer-schema validation. Python checks the exact four fields, consistent success/failure branches, finite JSON, 1 MiB size and nesting depth 64. Prose, fenced JSON, and arbitrary progress text are never used as fallback answers.

Runtime failure codes start with `anyo.`; the agent cannot claim that reserved namespace. Common codes include `timeout`, `outcome_unknown`, `invalid_output`, `provider_error`, `interaction_required`, `server_exited`, `connection_lost`, `configuration_failed`, `configuration_mismatch`, `version_mismatch`, and `executable_missing` (all prefixed `anyo.`). Runtime failure data is null; errors omit raw provider/transport bodies. `result.raise_for_failure()` explicitly raises `PromptFailed`. A hook that merely prints a failure and returns normally is still successful under the webhook contract. Durable workers record explicitly raised `anyo.timeout` and `anyo.outcome_unknown` as interrupted and do not automatically replay them, even if ordinary retries are enabled. Worker death can also leave effects uncertain; interrupted retries require separate opt-in and consumer idempotency.

## Progress and completion

Observers receive `PromptEvent` records with run/session/message/part IDs, a per-invocation sequence, timestamp, provider event name, kind, and optional message. Kinds include `status`, `assistant_text`, `text_delta`, `tool_start`, `tool_update`, `tool_end`, `reasoning`, `reasoning_delta`, `progress_gap`, and `terminal`. Snapshot events contain current text; delta events contain appended text. The terminal observer event contains status, while the return value carries authoritative data.

Reasoning is emitted only when requested and when exposed by OpenCode; no universal thinking stream is promised. Unknown deltas after a connection gap are suppressed until their type is known. The observer API does not forward tool input/output or raw process logs. Known configured keys/passwords are redacted from event messages, but model text may contain sensitive workspace content, and arbitrary split/encoded secrets cannot be guaranteed absent. Consumers control whether and where to log observer progress.

Enabling [operations collection](operations-ui.md#log-coverage-and-handling) separately records progress, prompt outcomes, exposed tool state/input/output, and the owned server's stdout/stderr, with redaction before storage. It works without `on_event` and preserves the observer/result contract. A shared server's raw lines are general logs because they cannot reliably identify a run; an external server's raw process streams are unavailable. Reasoning events still require `include_reasoning=True`. Collection is bounded and reports gaps/truncation; it does not claim to capture arbitrary detached/native process output or reconstruct older logs.

The observer buffer holds at most 256 events/1 MiB, with messages truncated to 8192 characters. Slow consumers lose oldest progress and receive `progress_gap`; final results use a separate path. Callback exceptions disable observation with a generic log message. Sync callbacks execute on the calling thread and must return promptly. Async callbacks run separately, must cooperate with cancellation, and get a 0.1-second final drain; a slow callback may miss terminal notification. Neither mode makes a hanging user callback safe to terminate forcibly.

The adapter subscribes to SSE before one `prompt_async` POST. HTTP 204 only means acceptance. It polls the latest assistant message, matches its parent user message, requires a completion timestamp and idle session, then validates `info.structured`. Disconnected SSE streams reconnect with an explicit gap; stored messages are reconciled without replaying the prompt. OpenCode has no guaranteed event replay. Ambiguous submission is never retried; expiry returns `anyo.outcome_unknown` when acceptance was uncertain.

The invocation deadline covers startup and execution; bounded abort/delete/process cleanup can extend return time beyond it. Cancellation propagates `CancelledError` after cleanup. Timed-out or canceled work may already have external effects. Each invocation owns a fresh session that is deleted after use. Unconfirmed cleanup on an owned server stops its process group, which can fail other active sessions. Client shutdown reaps the child and its remaining tool processes and removes private state.

## Docker deployment

Build at the repository root:

```sh
docker build -t anyo-opencode .
```

The image includes Python 3.12, Anyo with its OpenCode extra, verified OpenCode 1.18.34 and native OmO 5.1.21 binaries for amd64/arm64, Git, ripgrep, CA certificates, and `tini`. It runs as UID/GID 10001. No keys are passed at build time. Its default command runs the [minimal review example](../../examples/README.md#choose-opencode-or-native-omo); to run your own webhook pipeline, mount consumer code and a prepared workspace and provide secrets at runtime:

```sh
docker run --rm --name anyo-review \
  --env-file /absolute/path/to/runtime.env \
  -p 8000:8000 \
  -e ANYO_QUEUE_URL=sqlite:////state/data/review-queue.sqlite3 \
  -v anyo-review-queue:/state/data \
  -v "$PWD/examples:/app:ro" \
  -v /absolute/path/to/prepared-workspace:/workspace:ro \
  anyo-opencode python /app/opencode_review.py
```

The environment file supplies `ANYO_MODEL`, `MODEL_API_KEY`, `ANYO_GITHUB_WEBHOOK_SECRET`, `ANYO_GITHUB_ORGANIZATION`, and `ANYO_GITHUB_REPOSITORY`. Keep it outside the repository/build context. The example expects a prepared `pull-request.diff` with PR/head identification; a webhook provides identifiers, not a checkout or diff. Anyo does not post review comments automatically. The default image command is `python /app/main.py`; mounting a consumer application at `/app` replaces the bundled demo. To bake consumer code into an extended image, use `COPY --chown=10001:10001` and provide any tools its plugins need.

Python starts the child inside the same container using authenticated loopback HTTP. No Docker socket or privileged mode is needed. Only the webhook port is published. Another container can use `-p 8001:8000`; each has its own loopback and temporary state. Keep OpenCode session/private state separate between worker containers; mount only the queue data directory for intentional local SQLite sharing. Writable workspaces must be accessible to UID 10001. The queue volume must be writable by UID 10001; a new named volume inherits the image directory ownership. Give shutdown enough time to finish active hooks and their prompt deadlines; queued backlog stays in the volume. Docker's forced kill cannot complete cleanup. Set `ANYO_ROLE` to `receiver` or `worker` and use the same consumer registration code to split roles; only workers need model credentials for execution, though an application bootstrap that reads those variables still needs them or must make harness registration role-aware. For Redis, extend the image with the extra as shown in the [worker deployment guide](worker-queues.md#sqlite-and-docker-storage).

## Sources and verification

- User requests and implementation authorization, 2026-10-03 (conversation, no share link supplied).
- [Operations UI task](../tasks/protected-operations-ui/plan.md), authorized 2026-10-11, adds opt-in diagnostic capture independently of observer delivery.
- [Completed implementation task](../tasks/opencode-prompt-execution/plan.md) and [upstream research](../tasks/opencode-prompt-execution/research.md), including the pinned message-encoding workaround.
- [OpenCode server API](https://opencode.ai/docs/server/), [configuration](https://opencode.ai/docs/config/), and [v1.18.34 source](https://github.com/anomalyco/opencode/tree/v1.18.34).
- Local protocol tests exercise HTTP/SSE without model services. The [container smoke](../../tests/integrations/opencode/container_smoke.py) exercises the real binary with a local simulated model endpoint. The task records 420 passing tests in both checkout and clean installed-wheel runs, plus real-binary ARM64/AMD64 container checks. Paid-provider/model compliance requires the separately selected live test; simulation is not evidence of model quality. Select it explicitly with `ANYO_LIVE_MODEL` and `ANYO_LIVE_API_KEY` set, using `python -m pytest -m live tests/integrations/opencode/test_live.py`; it performs two bounded prompts and may incur provider charges.
