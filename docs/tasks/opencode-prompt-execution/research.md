# OpenCode integration research

## For Humans

Anyo needs a programmatic connection to OpenCode that can report progress and return a predictable final value. OpenCode already exposes the necessary server primitives, but its documentation and current release differ in details that matter to a Python adapter. This research separates verified upstream capabilities from the implementation recommendations in the [task plan](plan.md).

Status: draft  
Last updated: 2026-10-03  
Scope: official OpenCode and Docker documentation, OpenCode v1.18.34 source and release metadata, and Anyo's current runtime.

## Findings and recommended approach

Use a local `opencode serve` subprocess and call its HTTP API directly from Python. Use server-sent events (SSE) for progress and JSON-schema output for the final envelope. Install the executable beside Anyo in the consumer container; the Python process owns the child lifecycle. This is a recommendation derived from the capabilities below, not an upstream Anyo integration or a delivered API.

| Approach | Finding | Assessment for Anyo |
| --- | --- | --- |
| Headless HTTP server and SSE | Session creation, prompt submission, status/messages, abort, health, and event endpoints are exposed. | Recommended: separates invocation, progress, final results, and lifecycle. |
| `opencode run --format json` subprocess | The CLI documents its JSON format as raw events and can attach to an existing server. | Feasible alternative, but event serialization is not enforcement of Anyo's final envelope; process-per-call also repeats startup. |
| Official SDK | The documented SDK is a JS/TS package with generated HTTP types and server startup helpers. | Use its schemas as protocol evidence; do not add a JS service just to call HTTP from Python. This does not claim that no community Python client exists. |
| Separate OpenCode container | An official container image exists. | Optional deployment alternative; unnecessary for the user's requested same-container workflow. |

Evidence: [server API](https://opencode.ai/docs/server/), [CLI run command](https://opencode.ai/docs/cli/#run), [SDK](https://opencode.ai/docs/sdk/), and [installation options](https://opencode.ai/docs/).

## Version and evidence boundary

GitHub's release API reported **v1.18.34**, published **2026-09-30**, as the latest release when checked on 2026-10-03. Pin it as the initial implementation candidate and re-evaluate deliberately when implementing. The host has **v1.18.25**, checked with `opencode --version`; it was not upgraded or used to claim compatibility with v1.18.34. See the [release](https://github.com/anomalyco/opencode/releases/tag/v1.18.34) and [release metadata](https://api.github.com/repos/anomalyco/opencode/releases/tags/v1.18.34).

The initial research inspected official pages and tagged source without running a model. Implementation subsequently exercised the real 1.18.34 binary inside the root Docker image against a local simulated Anthropic endpoint; see the implementation findings below. No paid-provider call was made.

## HTTP contract and asynchronous execution

The relevant API surface is:

| Method and path | Purpose in the proposed adapter |
| --- | --- |
| `GET /global/health` | Confirm readiness and reported version. |
| `GET /doc` | Inspect the running server's OpenAPI contract. |
| `POST /session` | Create an isolated conversation for one invocation. |
| `GET /event` | Subscribe to events for the selected workspace. |
| `POST /session/{id}/prompt_async` | Submit a prompt; HTTP 204 is acceptance, not the final answer. |
| `POST /session/{id}/message` | Alternative request that waits for the response. |
| `GET /session/{id}/message` | Reconcile stored messages and retrieve structured output. |
| `GET /session/status` | Check session activity; an omitted session may be idle. |
| `POST /session/{id}/abort` | Request cancellation. |
| `DELETE /session/{id}` | Remove an owned session after extracting its result. |

The tagged prompt request accepts `parts`, `system`, `format`, `model`, `agent`, and an optional `messageID`. `prompt_async` forks the work and reports asynchronous errors through `session.error`; success must be established separately. These details are defined in the [route schema](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/server/routes/instance/httpapi/groups/session.ts), [route handlers](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/server/routes/instance/httpapi/handlers/session.ts), and [prompt input](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/session/prompt.ts).

Use explicit workspace routing consistently across sessions, messages, events, and aborts. The serve command identifies `x-opencode-directory` as a per-request input; it does not initialize one ambient project at server startup. Python must handle the pinned API's encoding of absolute paths, including spaces and non-ASCII characters. See [serve command](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/cli/cmd/serve.ts) and [instance context](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/server/routes/instance/httpapi/middleware/instance-context.ts).

## Structured output: useful capability, documented mismatch

The [SDK structured-output guide](https://opencode.ai/docs/sdk/#structured-output) describes `format={type:"json_schema", schema: ...}` and a `StructuredOutput` tool. It shows the response at `info.structured_output`, an optional `retryCount` defaulting to two, and a `StructuredOutputError` when output cannot be produced.

In v1.18.34, the [assistant-message schema](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/schema/src/v1/session.ts) instead declares **`structured`**. The [prompt loop](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/session/prompt.ts) writes the tool result there. It can set `StructuredOutputError` with `retries: 0` when the model finishes without a structured result. Although the input schema accepts `retryCount`, these findings do not establish that every failure receives the documented number of retries.

Implementation consequence: capture actual v1.18.34 HTTP responses, check the running specification, and test both valid and missing structured output. Pin extractor behavior to the verified server; do not guess at a field name or assume a retry guarantee from the guide. Anyo should validate its envelope after extraction regardless of upstream validation. Do not require a particular `finish` value in isolation: the loop can preserve a tool-call finish when structured output is produced.

An unrestricted inner JSON value is an Anyo design choice. The outer envelope describes success/failure; the user's prompt describes `.data`. Neither OpenCode's schema validation nor Anyo's wrapper can prove that the requested real-world action was completed or that a natural-language payload specification was followed.

## Progress, events, and recovery limits

The tagged [event endpoint](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/server/routes/instance/httpapi/handlers/event.ts) subscribes before emitting `server.connected`, filters by directory/workspace, and emits heartbeat events. Event JSON has an ID, but the SSE frame's `id` is unset; the handler contains no replay/`Last-Event-ID` recovery mechanism. Reconnecting should therefore trigger message/status reconciliation and an explicit possible progress gap, not a promise to replay every missed delta.

The [session schemas](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/schema/src/v1/session.ts) include text, reasoning, and tool parts, plus `message.updated`, `message.part.updated`, `message.part.delta`, and session errors. Parts and deltas carry message/session identifiers for correlation. The [status implementation](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/session/status.ts) publishes status changes and removes idle sessions from its active map; absence from `/session/status` is not evidence of a successful result.

Progress and final data must remain separate. Tool completion or step completion is not necessarily prompt completion. Reasoning availability depends on what OpenCode and the provider expose; no private reasoning access or universal “thinking” log is promised. The Python observer and backpressure interface is Anyo design work, not an existing upstream Python API.

## Server startup and isolation

The [tagged serve command](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/cli/cmd/serve.ts) prints the listening address. The [JS SDK startup helper](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/sdk/js/src/server.ts) starts a child and reads that announcement under a timeout. Anyo can use the same discovery principle while continuously draining both output pipes and validating authenticated readiness.

The [listener](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/server/server.ts) gives port zero special behavior: try 4096, then an OS-assigned port. Upstream [listener tests](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/test/server/httpapi-listen.test.ts) cover both cases. This supports collision-tolerant startup but does not allow assuming a specific port. Never attach automatically to an existing process just because it occupies 4096.

The server supports Basic authentication through `OPENCODE_SERVER_PASSWORD` and an optional username. Bind an owned child to loopback and authenticate even there. Basic auth is not transport encryption; any optional remote mode needs a private trusted connection or HTTPS. See [server authentication](https://opencode.ai/docs/server/#authentication).

Shared sessions do not isolate filesystem access or database state. The proposed default is per-runtime state and per-run sessions, with caller-provided separate workspaces when calls can mutate files. Multiple containers provide separate loopback namespaces; neither a shared host OpenCode daemon nor host-port allocation by Anyo is needed.

## Container installation

The official [v1.18.34 Dockerfile](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/Dockerfile) uses Alpine, musl binaries, and separate amd64-baseline and arm64 stages. Copying that binary into an unrelated glibc image is not a portable recipe. The reference Anyo image instead downloads the matching glibc release archive into a Debian-based Python image.

Release asset digests retrieved from GitHub:

| Target | Archive | SHA-256 |
| --- | --- | --- |
| `linux/amd64` | `opencode-linux-x64-baseline.tar.gz` | `24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27` |
| `linux/arm64` | `opencode-linux-arm64.tar.gz` | `bbdb3f00c2c51e42e315525233151309724226a8776da8e9145e3b0fa3d5310f` |

Source: [tagged release metadata](https://api.github.com/repos/anomalyco/opencode/releases/tags/v1.18.34). The tagged [build script](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/script/build.ts) archives the binary directory's contents, supporting extraction of the root `opencode` executable. Binary execution and native dependencies still need image smoke tests on both architectures.

Docker permits a service to own child processes and describes a minimal init for orphan reaping. The proposed image runs Tini, then the Python application; Python remains responsible for OpenCode startup, readiness, cancellation, and cleanup. See [multiple processes](https://docs.docker.com/engine/containers/multi-service_container/) and [multi-platform images](https://docs.docker.com/build/building/multi-platform/).

## Configuration and unattended operation

The user subsequently required a Python `anyo.harness(...)` API for provider, plugins, and model/key entries. OpenCode's [plugin documentation](https://opencode.ai/docs/plugins/#from-npm) uses a native `plugin` array and describes automatic package installation/cache at startup. Its [model configuration](https://opencode.ai/docs/config/#models) separates the selected model from provider configuration; [credential substitution](https://opencode.ai/docs/config/#env-vars) shows `provider.<id>.options.apiKey`. The adapter must translate Anyo's Python inputs rather than assume those field names match upstream. In particular, keys are provider-scoped, so differing keys for two models under the same provider need rejection or an explicit alias design. The plan proposes rejection for this first version.

OpenCode merges configuration sources. A custom config file does not automatically exclude repository config, and inline config can override conflicting settings without erasing all other keys. Use trusted runtime configuration and inspect effective settings. See [configuration precedence](https://opencode.ai/docs/config/#precedence-order).

Permissions can allow, deny, or ask; defaults include cases that ask, and a question tool exists. This matters because an unattended pipeline has no terminal user to answer. Recommend a scoped policy that permits intended tools and rejects unexpected interaction; surface any remaining request as a bounded failure. Do not solve a blocked request by blanket approval. See [permissions](https://opencode.ai/docs/permissions/).

The tagged [runtime flags](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/core/src/flag/flag.ts) include `OPENCODE_DISABLE_PROJECT_CONFIG`. Its exact coverage of project configuration, instruction files, plugins, and skills must be verified before claiming that an untrusted repository cannot alter execution. Mounted read-only files and a non-root user help the review example but do not make an unrestricted shell agent safe to run with arbitrary secrets.

## Remaining implementation checks

Confirm real HTTP and SSE fixtures, model compatibility with the envelope schema, authentication and workspace routing, permission/question termination, retry behavior, and clean cancellation of tools. Then verify the Linux image on both architectures and the installed Python package with its chosen transport dependencies. These are explicit completion criteria in the [plan](plan.md), not blockers to creating the task or reasons to add the later queue system now.

## Sources

Primary sources are linked beside the findings above. Local context came from the root [README](../../../README.md), [product direction](../../knowledge/product-overview.md), [webhook contract](../../knowledge/webhook-triggers.md), `src/anyo/application.py`, `src/anyo/execution.py`, `src/anyo/__init__.py`, and `pyproject.toml`. User requirements came from the 2026-10-03 conversation (no share link supplied).

## Implementation findings from the real binary

The [container smoke](../../../tests/integrations/opencode/container_smoke.py) exercises 1.18.34 with Python-controlled model credentials/base URL, a local plugin, conflicting ambient project config, live progress, and three isolated sessions. It confirms `info.structured` as the final result field. Model responses in this test come from a local HTTP fixture, so it verifies transport and lifecycle rather than model instruction-following.

Two details required changes from the initial design:

- A deny-all permission rule also removes OpenCode's internal `StructuredOutput` tool. Anyo must add a final allow rule for that reserved tool in the agent and session permissions. Without it, the real server finishes with `StructuredOutputError` even when the schema is supplied.
- Retrieving persisted user messages with a JSON output format returns HTTP 400 in the tested release: `Expected OutputFormatJsonSchema` at `[0]["info"]["format"]`. The adapter fetches `/session/{id}/message?limit=1`, tolerating only this exact format-encoding error while the latest message is the user prompt. Once an assistant message exists, the latest-message endpoint returns it correctly. The single-prompt session's assistant `parentID` recovers correlation if the user event was missed. Completion still requires idle status, a completed assistant message, and valid structured data. The host's 1.18.25 showed the same encoding issue; it was not adopted as a fallback.

The latest-message workaround is deliberately tied to the exact version check. Future upgrades must rerun the real-binary smoke and revisit both the workaround and the child-only `OPENCODE_TEST_HOME` isolation flag. See the [task outcome](plan.md) for final verification evidence and limits.
