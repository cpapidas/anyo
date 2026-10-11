# Webhook triggering contract

## For Humans

A consumer connects a Python function to a GitHub, Slack, or Better Stack event and starts Anyo from the project's main file. Anyo authenticates webhooks, selects matching hooks, and runs those functions in the background; Slack interactions can also use an immediate response handler. This document records the shared contracts and delivery limits.

Status: draft  
Last updated: 2026-10-11
Scope: implemented [webhook triggers](../tasks/webhook-triggering-system/plan.md), immediate responses, and their integration with [durable workers](worker-queues.md). The worker task records completed verification; detailed APIs are engineering decisions within the authorized implementation.

## Registration and callbacks

`anyo.add_hook(provider=..., action=..., pipeline=None, secret=None, responder=None, hook_id=None, hook_version="1", retry=None, **configuration)` returns a `Hook` with a process-local identifier. At least one callback is required. GitHub requires `organization` and `repository` strings and supports only `pr_created`. Names match case-insensitively, and `organization` may also name a personal account. Slack supports `event`, `interaction`, `slash_command`, and `control`, with native selectors and case-sensitive app/installation IDs described in the [Slack guide](slack-hooks.md). Invalid providers, actions, configuration, duplicate registrations, and callbacks that cannot accept one positional event fail at registration.

Several distinct callbacks may subscribe to the same event. Every matching hook runs independently; an exact repeated registration of the same callable, normalized configuration, and secret is rejected. Registrations are frozen while the application runs and may be changed after it stops. The application retains registrations across sequential starts. SQLite/Redis delivery history persists; only memory mode resets it. An empty application and concurrent/repeated starts while running are errors.

Each pipeline receives one `HookEvent` with `provider`, `action`, `delivery_id`, `organization`, `repository`, `pull_request_number`, `head_sha`, and `payload`. Repository strings default to empty and PR fields to `None` for other providers; existing positional construction stays compatible. Slack supplies a `SlackEvent` subclass with its own optional context fields. Each invocation receives its own payload copy. Plain functions execute in worker threads; async functions execute on the runtime's asyncio loop. Async callbacks must not block that loop. Pipeline return values are discarded. Ordinary exceptions mark that hook's delivery failed and are logged with its hook identifier and exception type, without payloads or exception messages by default. Callback signatures must be inspectable and accept one positional event.

Durable pipeline registrations use stable identities from `hook_id` or derivable top-level functions. Closures, lambdas, bound methods, and callable instances need an explicit `hook_id`; they are still executed through the loaded registry. `hook_version` and retry/routing settings participate in namespace compatibility. See [identity and migration rules](worker-queues.md#identity-and-namespace-compatibility).

The module-level functions use a default application with GitHub, Slack, Better Stack, and SQLite storage. `anyo.application.Application` provides isolated registries for tests or multiple applications, accepting explicit provider and queue adapters; its historical memory default is preserved unless durable storage is composed. Provider contracts live in `anyo.hooks`; adapters live in `anyo.integrations.github`, `anyo.integrations.slack`, and `anyo.integrations.betterstack`. The package root wires them without network or startup side effects.

[Better Stack](betterstack-hooks.md) supports `incident` and `error`, with explicit event selection and provider-owned context. It returns an empty `200` after admission and authenticates a custom secret header; GitHub and Slack retain their signature verification. Its total provider deadline defaults to five seconds. `BetterStackEvent` preserves native context through the provider's versioned event codec.

## Provider packages and extension contract

Each concrete provider owns a package under `src/anyo/integrations/<provider>/`. The user selected this organization so that configuration and event handling for each provider stay together as more providers are added. GitHub is the first example:

```text
src/anyo/
├── hooks.py                          # Shared Provider, Hook, and HookEvent contracts
├── application.py                    # Registration, HTTP reception, and lifecycle
├── execution/                        # Queue contracts, workers, memory/responses
└── integrations/
    └── github/
        ├── __init__.py               # Stable GitHubProvider export
        ├── _provider.py              # Implements the shared adapter contract
        ├── _configuration.py         # GitHub options and identifier validation
        └── _webhooks.py              # Signature verification and event translation
```

`from anyo.integrations.github import GitHubProvider` remains the public import. Modules prefixed with `_` are internal; consumers do not import them. Provider behavior tests live in `tests/integrations/github/`, while the shared receiver and execution tests stay with their capabilities.

The existing `Provider` protocol is the abstraction used by the application:

| Member | Responsibility |
| --- | --- |
| `name` | Unique URL-safe provider key used by hook registration and `/webhooks/{provider}`. |
| `secret_env` | Environment variable used for the default webhook secret at startup. |
| `validate(action, configuration)` | Reject unsupported actions/options and return a normalized configuration snapshot. |
| `decode(body, headers, secret)` | Authenticate raw bytes and return a `HookEvent`, `None` for an irrelevant event, or a response-aware `WebhookDelivery`. |
| `matches(configuration, event)` | Apply provider-specific filters to a verified event. |

Providers implement this protocol structurally; they do not need to inherit a base class. Validation, decoding, and matching are local operations without network I/O or pipeline execution. The shared application retains responsibility for reading HTTP requests, resolving secrets, selecting adapters, and submitting matched jobs. Its code does not branch on provider names or understand provider-specific payload fields.

Header names passed to adapters are lowercase; repeated values are joined with comma-space. Adapters must reject ambiguous credentials. The generic failed-authentication response says `Invalid webhook authentication`; provider-specific validation remains responsible for signatures or shared secrets as appropriate.

For a new provider, create its own package, expose its adapter through that package's `__init__.py`, and pass an instance to `Application(providers=[...])`. Add configuration and delivery modules only when that provider needs them. Built-in default providers are wired explicitly at Anyo's package root; introducing an adapter does not require changes to the receiver or dispatcher. There is no automatic filesystem/plugin discovery, and importing `anyo.integrations` does not import every provider. This keeps optional provider dependencies local to the integrations that use them. Providers with custom event subclasses supply an `event_codec` or a consumer registers `event_codecs` on `Application`; the [codec guide](worker-queues.md#event-codecs-for-custom-providers) explains finite JSON transport without importing classes from queued data.

`WebhookDelivery` carries an optional event, a `WebhookResponse` acknowledgment, an optional response renderer, and a `requires_response` flag. `WebhookResponse` contains bytes, HTTP status, content type, and the dispatch decision. A setup challenge can return a delivery without an event. The optional structural `ResponseProvider` protocol adds a positive `request_timeout` covering the whole request and response-aware decoding. Only these providers accept `responder=`. Existing event-or-`None` adapters need no changes and retain accepted-count JSON responses. Code directly consuming the widened `Provider.decode` return type must narrow it before accessing event fields.

An async responder returns `HookResponse(body=..., dispatch=True)`; a provider-owned renderer validates and encodes it. A responder-only hook may omit its pipeline. Conflicting matching responders receive `409` before consumer code runs. Required response data without a matching responder, responder failure, deadline expiry, or response-capacity exhaustion produces `503`. Background jobs are admitted atomically after the response decision; `dispatch=False` suppresses them. Deadline checks also cover synchronous matching and payload copying before admission.

Immediate responders have their own concurrency bound equal to `workers`, no waiting queue, and a separate cache bounded by `max_records` and `retention_seconds`. Concurrent duplicates share a task, and completed successful replies are replayed even if a later background admission fails. Failed/cancelled replies may retry. Request cancellation cancels the shared responder, which can affect other duplicate waiters. Its slot remains occupied until it exits. This cache remains process-local and resets at restart, independently of durable pipeline history. Shutdown cancels and awaits responders before finishing owned pipeline attempts. Cooperative cancellation and bounded external calls are required; blocking code can defeat an event-loop deadline. The [Slack guide](slack-hooks.md) documents its response shapes, identity rules, and deadline.

## Startup and configuration

`anyo.start()` defaults to SQLite at `.anyo/queue.sqlite3`, namespace `default`, and role `all`; it blocks and serves registered providers at `127.0.0.1:8000` using Starlette and Uvicorn. Keyword overrides select `host`, `port`, `workers` (pipeline concurrency, default 4), `capacity` (running plus queued jobs, default 100), `retention_seconds` (default 3600), `max_records` (default 10000), `max_body_bytes` (default 1048576), and `request_timeout` (body-read deadline, default 5 seconds). Slack also has a total 2.5-second provider budget; whichever deadline expires first applies. `port=0` is allowed for OS allocation. The provider path is `/webhooks/{provider}`; its format is fixed in this version. `queue_url`, `queue_namespace`, `role`, `lease_seconds`, `poll_interval`, and `operation_timeout` configure durable execution; their defaults and backend switching are in the [worker guide](worker-queues.md). `role="worker"` serves no HTTP, while `role="receiver"` runs no background pipeline.

Secrets are passed per hook as `secret=...` or resolved at startup from `ANYO_GITHUB_WEBHOOK_SECRET`. Missing or empty secrets fail before serving. Worker-only startup consumes already verified jobs and does not require signing secrets. Hooks may use different secrets, including for different repositories. A delivery is eligible only for hooks whose own secret validates it; another repository's valid signature grants no access to those hooks. Secrets are excluded from hook representations and log messages.

GitHub repository webhooks are configured manually in this version. The consumer supplies a publicly reachable HTTPS URL through deployment or a local forwarding tool, subscribes to pull request events, selects JSON, and sets the matching secret. No GitHub API token is needed to receive deliveries; pipeline code needs its own credentials if it calls GitHub APIs. Starting Anyo does not provision a public URL, register remote webhooks, or perform checkout.

`Application.asgi(**runtime_options)` returns a Starlette application for in-process tests or an existing ASGI host; its lifespan must be used and its backend must be asyncio. `Application.start(...)` uses that same application for roles `all`/`receiver` and owns its listening socket. `Application.worker(...)` and `anyo.worker(...)` provide an async worker context without HTTP; `start(role="worker")` owns its blocking process lifecycle. Bind errors propagate as exceptions. Startup inside an already running event loop is rejected with guidance to use the ASGI application. The library does not configure the root logger, change working directory, or call `sys.exit`.

On normal durable shutdown or the first interrupt, stop admission/claims, finish owned active attempts, and preserve unclaimed backlog for remaining or restarted workers. Memory mode retains its full drain of accepted work. There is no forced pipeline timeout: Python cannot safely kill arbitrary synchronous functions. Consumers must put deadlines on their own external calls; a hung callback can delay shutdown. Forced termination leaves durable work for lease recovery and can lose memory-mode work. ASGI hosts control their own lifecycle.

## Reception and execution

GitHub `pull_request` plus payload action `opened` maps to `pr_created`. Routing uses the target repository, including PRs from forks. The adapter verifies HMAC-SHA256 against the original body before parsing or dispatch. Authenticated setup pings and unsupported events are acknowledged without executing a pipeline.

GitHub responses are `202` with an accepted-job count after admission and `200` with count zero for ignored or duplicate deliveries. Slack returns `200` with its protocol-specific response after successful handling. Both use `400` for malformed deliveries, `401` for invalid/missing signatures, `413` for oversized bodies, `408` for body-read timeouts, and `503` when unavailable or at capacity. Unknown provider paths return `404`, and non-POST methods return `405`. The whole newly eligible group is admitted or rejected together; no partial fan-out occurs on capacity rejection. Durable acceptance follows confirmed storage admission under the backend persistence policy. It does not mean successful execution; memory-mode acceptance means queued only in this process. Unsupported event serialization returns `400` before any job is admitted.

Durable workers claim the earliest eligible queued jobs with bounded concurrency. Completion order is unspecified, and ordinary callback failure is isolated. Accepted and delayed-retry jobs count against shared capacity until terminal. Active and successful records suppress redelivery per stable hook/provider delivery identity. Failed/interrupted records permit explicit retry without rerunning successful sibling hooks, using the original admitted event while history is retained. Automatic retries are opt-in through `RetryPolicy`; Anyo does not fetch missed provider deliveries.

Durable attempts have renewable leases and fenced completion tokens. Lease expiry becomes interrupted unless interrupted replay was explicitly enabled. Cancellation, process-control errors, lost ownership, and backend failures stop local admission/claims; owned callbacks retain cleanup ownership. Unclaimed jobs remain persisted. Memory mode keeps its prior behavior of failing queued work after fatal worker failure. The [worker recovery contract](worker-queues.md#admission-execution-and-recovery) records OpenCode uncertainty, retry limits, and external-effect caveats.

Terminal records expire from completion time; queued/running records never expire for retention. A full retained history rejects new IDs with `503` until space becomes available. SQLite/Redis history survives application restart and is shared within the namespace; memory history resets and cannot deduplicate across server processes. Redelivery after expiry or backend cutover can run again. Exactly-once external effects are not provided.

## Tooling

The initial baseline is Python 3.12, with standard `venv`/`pip`, setuptools for packaging, and `build` for source-to-wheel verification. Starlette 1.7.0 and Uvicorn 0.53.0 are the tested runtime lower bounds; they are base dependencies so the default installation can start its listener. GitHub verification needs no SDK or optional extra. Pytest, HTTPX2 (used by Starlette's test client), Ruff, and mypy are development tools. The distribution uses the local name `anyo` and initial development version `0.1.0.dev0`; no index-name ownership, publication, license, or release policy is implied.

## Sources

- Project conversation, 2026-10-03 (no share link supplied): requested registration and startup API, GitHub-first webhook design, then authorization to implement the task.
- Project conversation, 2026-10-03 (no share link supplied): requested a dedicated folder for each hook provider and a consistent abstraction for adding providers; [provider package organization](../tasks/provider-packages/plan.md) implements that direction without changing consumer calls.
- [Product direction](product-overview.md) and [implementation task](../tasks/webhook-triggering-system/plan.md).
- [GitHub webhook practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks) and [signature verification](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries): authentication, prompt acknowledgment, and delivery identity constraints.

- Project conversation, 2026-10-04 (no share link supplied): authorized the [SQLite and Redis worker task](../tasks/sqlite-redis-workers/plan.md); [worker queues](worker-queues.md) record its implemented execution contract.
