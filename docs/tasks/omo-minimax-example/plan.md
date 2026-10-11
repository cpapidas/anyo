# Install OmO and demonstrate a MiniMax review

## For Humans

This completed experiment installed the OpenCode edition of OmO and demonstrated a delegated MiniMax review. It is retained as historical evidence. The user subsequently requested native OmO, so the plugin example and its live test were removed and replaced by the [native harness task](../native-omo-harness/plan.md) and [selectable review example](../../../examples/README.md#choose-opencode-or-native-omo).

Status: done  
Last updated: 2026-10-06

## Objective and scope

Install a pinned OmO plugin locally, integrate it through Anyo's existing public API, and run live tests with MiniMax exclusively. Provide a repeatable standalone example without requiring a webhook, posting reviews, editing consumer code, or changing Anyo's public API. The supplied credential belongs only in private local state or the environment.

Related knowledge: [product direction](../../knowledge/product-overview.md), [OpenCode contract](../../knowledge/opencode-prompts.md), and [existing integration research](../opencode-prompt-execution/research.md).

## Completion criteria

- OpenCode 1.18.34 and oh-my-openagent 5.1.19 are installed through a repeatable local setup.
- Anyo loads the actual OmO plugin and uses its `call_omo_agent` tool to delegate to `explore`.
- The parent and child use `minimax-coding-plan/MiniMax-M2.7`; other model routes fail closed.
- The child reads the fixture, identifies the empty-list division by zero, and the parent returns a structured result to Python.
- The fixture remains unchanged, owned processes close, and credentials are absent from source and reports.
- The example is documented, its explicit live test passes from outside the checkout, and relevant lint/syntax checks pass.

## Implementation

The former `examples/omo-minimax` directory and its setup/run scripts have been removed. They installed packages under the ignored `.anyo/omo` directory, leaving the shell's existing OpenCode 1.18.25 available. The supplied credential now lives under `.anyo/credentials/minimax.key` for the replacement example. The implementation and commands below describe the historical experiment, not the current setup path.

OmO 5.1.19's config hook adds permission rules and changes agent configuration. Anyo intentionally checks that its owned configuration remains effective, so directly loading OmO would conflict with that contract. The example's `plugin.mjs` invokes the real OmO plugin and restores Anyo's provider/model/permission settings after its config hook. It pins all resulting agents and guards `chat.params`; the Python-generated OmO config pins categories and disables fallback. This stays in the consumer example rather than adding OmO-specific behavior to Anyo.

The fixture deliberately contains `sum(prices) / len(prices)` without an empty-list guard. The parent must delegate, the child must actually read, and success requires both the expected finding and session/model evidence. Tool audit entries contain only names and session IDs; model entries contain identifiers, not prompts, responses or keys. The Python result also checks the fixture byte-for-byte.

## Verification

Completed on macOS on 2026-10-06:

- `bash examples/omo-minimax/setup.sh`: installed the pinned packages and verified the two versions; repeating the command succeeded.
- Initial Anyo connectivity check and the delegated OmO review: successful real MiniMax M2.7 responses.
- `ANYO_LIVE_OMO=1 .venv/bin/python -m pytest -m live tests/integrations/opencode/test_live_omo.py -q`: **1 passed in 21.96 seconds**, launching from outside the checkout. The parent used two generation steps and `explore` used three, all reporting `minimax-coding-plan/MiniMax-M2.7`. The audit recorded the child's `glob` and `read` tools and the completed OmO delegation; the fixture remained unchanged. Local evidence is in `.anyo/omo/runs/review-_c22_yk8/{result.json,omo-audit.jsonl}`.
- A separate negative check deliberately selected MiniMax M2.5 instead of the pinned M2.7: the wrapper rejected the invocation (`anyo.provider_error`), with no generation calls accepted by its model guard. No non-MiniMax provider/model was selected for any live check.
- Selecting the live test file without `-m live` reported **1 deselected**, confirming that ordinary test runs do not trigger this paid example. Pytest exits 5 when that file is the only selection and all tests are deselected.
- Ruff check and format check passed for the example and live test; mypy passed for all 32 library source files. `node --check` and `bash -n` passed for the wrapper and setup script.
- Local reproduction confirmed `average_price([])` raises `ZeroDivisionError` while `[10, 20]` returns `15`. Credential and documentation-link scans passed; the credential file is mode `0600`, and no example-owned processes remained.

Some responses put JSON text in the envelope's unconstrained `data`; the example normalizes that value without changing Anyo's general result contract. OmO's transitive TUI dependency emitted a Node engine warning on Node 22; the verified headless plugin path uses OpenCode's bundled runtime. This task did not change library code, package metadata, global CLI binaries, or the Docker image, and did not validate every OmO tool or its interactive/standalone editions.

## Sources

- User request, 2026-10-06 (no share link supplied): read project context, install OmO, work autonomously, test it and deliver a clear example using the supplied MiniMax key and no default models.
- [OmO upstream installation](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/installation.md) and [configuration](https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/reference/configuration.md), inspected 2026-10-06; the installed 5.1.19 package was inspected for the actual config and plugin interfaces.
- [MiniMax endpoints and OpenCode integration](https://platform.minimax.io/docs/token-plan/other-tools), inspected 2026-10-06; the installed OpenCode catalog lists `minimax-coding-plan/MiniMax-M2.7`, and the live request verified the supplied key against the MiniMax endpoint.
