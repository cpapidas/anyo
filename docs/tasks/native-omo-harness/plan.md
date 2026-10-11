# Add native OmO as a selectable harness

## For Humans

The first OmO example loaded it as an OpenCode plugin, while OmO now also offers its own standalone agent harness. This task added native OmO to Anyo's existing harness interface and replaced that plugin example with a choice between the two executables. The later [minimal example task](../minimal-harness-example/plan.md) replaces the original multi-file example and adds both executables to Docker; the verification below records this task's earlier state.

Status: done
Last updated: 2026-10-06

## Objective and scope

Implement `anyo.harness(provider="omo", models=[...])` beside `provider="opencode"`, preserving `PromptClient`, sync/async hooks, results and progress. Own the native process and its temporary state, validate its model configuration and envelope, remove the old plugin example, and supply a runnable replacement. Keep unrelated execution architecture and existing OpenCode behavior unchanged.

Dependencies: [native OmO contract](../../knowledge/omo-prompts.md), [OpenCode contract](../../knowledge/opencode-prompts.md), shared `anyo.agents` contracts, and pinned native OmO 5.1.21. This supersedes the [plugin example](../omo-minimax-example/plan.md), whose task remains as history.

## Completion criteria

- Both built-in harnesses are selectable through the public Python API.
- Native OmO runs directly through classic stdio RPC and requires no OpenCode/plugin installation.
- Registration is inert; prompt timeout, capacity, cancellation, shutdown, credential isolation and failure envelopes are exercised.
- A native delegated review and an OpenCode direct review both succeed using MiniMax M2.7, with outbound request-model evidence.
- The replacement example is documented; the obsolete example and test are removed.
- Required local tests, lint, types, wheel/source build, metadata and clean installed-package checks pass.

## Implementation decisions

The adapter lives in `src/anyo/integrations/omo/`, with configuration, a subprocess runtime and a packaged native bridge extension. The package root registers its builder alongside OpenCode. OmO uses Python's standard library, so it adds no Python dependency; the standalone executable is installed separately. The package includes `_bridge.mjs` as a required runtime resource.

Each prompt owns an ephemeral native process. A readiness handshake and native `get_state` response must agree before a user prompt is sent. This matters because native OmO logs an extension-load failure and can otherwise continue without it. The bridge supplies a terminating StructuredOutput tool, restricts the model catalog and active tools, and exits before an unregistered parent provider request: ordinary extension exceptions are caught by the upstream runner and therefore are insufficient as a blocking guard.

Native task children use the restricted registry. The original example invoked the curated in-process `explore` agent with an explicit model; its live tests used a local forwarding recorder to check the parent and child request model at the provider boundary. Arbitrary background teams/daemon children and all OmO tools were not promised by this task. At that point, the Dockerfile installed OpenCode only; the follow-up task adds native OmO.

## Verification

Completed on macOS ARM64 with Python 3.12.14 on 2026-10-06:

- Both example setup commands succeeded: native OmO **5.1.21** with Senpi **2026.10.10-3**, and OpenCode **1.18.34**. Native installation was repeated successfully with shell-profile modification disabled.
- Full default suite: **537 passed, 18 deselected**. The native adapter accounts for **35** deterministic tests covering inert registration, configuration, fresh sessions, structured results, progress redaction, provider and protocol failures, initialization, JSON escaping, timeout, cancellation, capacity and process-group cleanup. Paid live tests are excluded by default.
- `ruff check src tests examples/harness-review`, the corresponding format check, mypy over **35 source files**, `node --check` for the bridge, and `bash -n` for setup passed. Unscoped `ruff check .` also walked downloaded upstream scripts under ignored `.anyo/` and found a pre-existing long comment in `examples/queued_webhook.py`; these unrelated files were not edited.
- Explicit live tests passed for both harnesses, first in the development environment and then with a clean installed wheel from outside the checkout. The final installed run was **2 passed in 40.11 seconds**. Native OmO completed its `task` delegation and both paths returned the expected `ZeroDivisionError` finding without changing the fixture. The prompt does not supply the expected exception name.
- The final HTTP recorder captured **5 native OmO requests** (parent and child) and **2 OpenCode requests**, all with `model="MiniMax-M2.7"`, plus tool names confirming actual reads. It forwarded only that model to the official MiniMax endpoint, accepting OmO's valid `?beta=true` query. Safe evidence is saved in `.anyo/harness-review/verified-omo.json` and `verified-opencode.json`; no prompts, headers or keys are stored in the request audit. No non-MiniMax live models were selected.
- Built the source archive and a wheel from that archive in `dist/native-omo-harness-20261006/`, inspected metadata/resources, and passed `twine check`. Final artifacts in `dist/native-omo-harness-final-20261006/` also include the final live-test source. The required `_bridge.mjs`, envelope resource and `py.typed` are included; credentials and local state are absent.
- Installed the wheel in a fresh temporary environment outside the checkout. The base-only smoke passed with neither `httpx2` nor Redis installed, imports resolved to `site-packages`, and `pip check` passed. All **35 native tests** passed against that installation; a native MiniMax review then passed with no OpenCode extra. Adding the OpenCode extra enabled the final two-harness live run.
- Local documentation links and credential scans passed. The supplied credential remains mode `0600` under ignored `.anyo/credentials/`. The obsolete plugin example and its live test are absent.

The source and live checks validate the documented native adapter and synchronous curated delegation. Other Python versions, native OmO on Linux, arbitrary background/daemon teams, and an OmO Docker image were not exercised. This was a local development build; no package was published.

## Sources

- User follow-up, 2026-10-06 (no share link supplied): remove the OmO plugin example and let users choose OpenCode versus native OmO; continue autonomously.
- [OmO upstream](https://github.com/code-yeongyu/oh-my-openagent), [installation](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/installation.md), and [native task behavior](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/senpi-task.md).
- Installed `omo-ai@5.1.21` and `@code-yeongyu/senpi@2026.10.10-3` source and documentation; pinned macOS ARM64 binary SHA-256 `c1fa1024326140432f8d5beb1063a928a65aff696638973c19fa78264e941628` verified by the installer.
