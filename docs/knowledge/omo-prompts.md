# Native OmO prompt execution

## For Humans

A Python pipeline can choose native OmO or OpenCode while keeping the same prompt client and result handling. The native OmO adapter launches the standalone `omo` executable, streams progress, and validates Anyo's structured result envelope. This guide covers that adapter's configuration and operational limits.

Status: accepted  
Last updated: 2026-10-11  
Scope: native OmO 5.1.21, using its bundled Senpi engine 2026.10.10-3.

## Choose a harness

```python
import os
import anyo

handle = anyo.harness(
    provider="omo",  # Use "opencode" for the existing OpenCode adapter.
    models=[{"model": "minimax/MiniMax-M2.7", "key": os.environ["MINIMAX_API_KEY"]}],
    executable="omo",
)
with anyo.PromptClient(harness=handle) as client:
    result = client.inject_prompt("review.md", workspace="/path/to/project")
    result.raise_for_failure()
    print(result.success.data)
```

The [Dockerfile](../../Dockerfile) installs **native OmO 5.1.21** and **OpenCode 1.18.34**, with the [two-file example](../../examples/README.md#choose-opencode-or-native-omo) as its default command. Outside Docker, install the Python library and the native OmO binary separately; this adapter adds no Python dependency or optional extra. OmO's npm distribution is `omo-ai`; the OpenCode plugin package `oh-my-openagent` is not used by this harness. Importing Anyo and configuring the harness do not start processes or resolve credentials.

Model names belong to the selected harness's catalog. The same MiniMax service uses `minimax/MiniMax-M2.7` in native OmO and `minimax-coding-plan/MiniMax-M2.7` in OpenCode. Native OmO's MiniMax `base_url`, when supplied, is `https://api.minimax.io/anthropic`; OpenCode's corresponding base includes `/v1`. The adapter supports OmO's built-in model catalog, with optional endpoint overrides; it does not add a custom model-catalog API.

For custom application composition, register `omo_harness` from `anyo.integrations.omo` in `Application(harnesses={"omo": omo_harness})`. The package root already registers both built-in adapters. Applications still register one harness before startup; a client invocation can select another model registered with that harness using `model=`.

## Same Python prompt contract

The existing `PromptClient`, `inject_prompt`, `ainject_prompt`, context, workspace, timeout, progress observer, and `PromptResult` APIs apply. Hooks inherit their application's selected harness, including SQLite/Redis workers. See the [shared call examples and result contract](opencode-prompts.md#prompt-and-result-contract).

Anyo installs a small native extension into each invocation's private temporary state. It supplies the `StructuredOutput` tool, a readiness handshake, the registered-model guard and exact tool allowlist. This is a bridge inside native OmO, not OmO loaded into OpenCode. Python waits for the handshake and checks the selected model before submitting the user's prompt. It accepts the final tool envelope only after the agent reaches its terminal event.

The `data` field remains any valid JSON value. A provider may choose to encode a requested JSON object as a string; a consumer that requires an object must validate or normalize that application-level value, as the example does. Anyo does not silently change general result shapes.

## Options

| Option | Default | Behavior |
| --- | --- | --- |
| `executable` | `"omo"` | Standalone native executable; version must be 5.1.21. |
| `tools` | `("read", "grep", "find", "ls")` | Exact tool names. `StructuredOutput` is always included. Unknown tools fail initialization. |
| `max_concurrency` | `4` | Active invocations per client; overflow returns `anyo.capacity_exceeded`. |
| `startup_timeout` | `30` | Deadline for version check and initialization, also bounded by the invocation timeout. |
| `shutdown_timeout` | `5` | Grace period before killing the owned process group. |
| `state_directory` | System temporary directory | Parent for private per-invocation state, removed during cleanup. |
| `plugins` | Empty | Nonempty values are rejected; OpenCode plugin specifications do not apply. |

`models` accepts `model`, `key` and optional `base_url`. Models under one provider must share credentials and endpoint. Only registered models remain in the private native catalog; other providers and implicit fallback are disabled. The bridge terminates its process if a parent provider-request hook resolves an unregistered model. Native in-process children use the restricted model registry too. The live tests additionally verify actual outbound MiniMax request models at an HTTP recorder.

To use native delegation, explicitly include `"task"` in `tools` and name a registered model in a task addressed to `subagent_type="explore"` or `"librarian"`. The [original adapter task](../tasks/native-omo-harness/plan.md) records successful synchronous `explore` delegation; the current minimal example performs a direct review. Category routes depend on OmO's own category configuration; arbitrary teams, background continuations, host-daemon children, custom extensions and every other OmO tool have not been validated by this adapter. Tools are deliberately opt-in, and their permission policy is not an operating-system sandbox.

## Process and credential ownership

Each invocation owns a fresh native OmO process and ephemeral session, even when a `PromptClient` is reused. The adapter uses classic JSON-lines RPC over stdin/stdout. It does not attach to a global OmO daemon or require an OpenCode server. Local management currently supports POSIX systems; validation includes macOS and the Linux Docker image.

The child gets a private `OMO_CODING_AGENT_DIR`, explicit model and tools, disabled fallback and automatic model selection, and disabled ambient project settings, skills and context-file discovery. The adapter disables OmO memory, telemetry, external MCPs, automatic continuation components, config migration/watch, and unrelated service integrations for its invocation. Native OmO can still read its normal layered task configuration; model availability and the active tool policy are independently constrained by the bridge.

Credentials are passed in private child environment variables and referenced from the generated catalog, never command arguments or serialized config. The environment preserves the real home directory, executable path, locale and transport settings but excludes ambient provider credentials and agent overrides. This is not a general environment-forwarding API. Without operations collection, raw native stderr is drained without retention. Progress messages redact registered keys and omit reasoning unless explicitly requested.

Opt-in [operations collection](operations-ui.md#log-coverage-and-handling) retains redacted owned stderr, supported progress, exposed tool execution records and diagnostics, and prompt outcomes even without `on_event`. Its run/attempt/prompt context distinguishes concurrent invocations. JSON-RPC stdout remains interpreted protocol rather than an unfiltered raw log, and reasoning events require `include_reasoning=True`. Collection is bounded, shows gaps/truncation, and cannot promise output from arbitrary detached tools or recover earlier logs. Redaction covers known credentials, not every unknown or transformed secret in model/workspace text.

Success, failure, timeout and cancellation close the owned process group and remove temporary state. A provider failure produces `anyo.provider_error`; missing initialization/configuration, invalid JSON/protocol, missing result, unexpected interaction, process failure, unavailable executable and version mismatch are distinct runtime failures. As with any tool-capable harness, processes intentionally detached by explicitly allowed tools are not an OS containment guarantee.

## Deployment and verification

The Docker image includes both pinned harnesses on `PATH`, verified against their release checksums for ARM64 and AMD64, and runs as UID 10001. Build with `docker build -t anyo:local .`; run `docker run --rm -e MINIMAX_API_KEY -e HARNESS=omo anyo:local` for the bundled review, or select `HARNESS=opencode`. No credentials are needed during the build. Mount consumer code at `/app` or override the command to use your own application.

The [adapter task](../tasks/native-omo-harness/plan.md) records its original live, local, and installed-wheel checks. The [minimal example task](../tasks/minimal-harness-example/plan.md) records the two-file example and container verification.

Sources: user follow-up on 2026-10-06 requesting a native harness choice; [operations UI task](../tasks/protected-operations-ui/plan.md), authorized 2026-10-11, for opt-in diagnostic capture; [OmO installation](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/installation.md); [native task guide](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/senpi-task.md); [native configuration](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/reference/omo-json.md); installed `omo-ai@5.1.21` and `@code-yeongyu/senpi@2026.10.10-3` RPC, extension, model and settings documentation and source.
