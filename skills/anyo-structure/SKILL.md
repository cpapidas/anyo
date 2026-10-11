---
name: anyo-structure
description: Structure Anyo as a Python library. Use when adding or moving Python modules, assigning capability ownership, changing public imports or component dependencies, or reviewing package organization. Covers the src layout, module boundaries, integrations, tests, examples, and module growth.
---

# Anyo package structure

## For Humans

Developers will install Anyo and import its capabilities into their own Python code. This skill keeps those capabilities easy to locate and the public API stable as the library grows from small modules into larger components.

## Apply the agreed structure

- Resolve paths from the project root containing this file. Read the root [README](../../README.md), applicable `AGENTS.md` files, and the [product direction](../../docs/knowledge/product-overview.md) before structural changes.
- Build one distributable Python library with the import package `anyo` under `src/anyo/`. Keep consumer applications outside the library's implementation.
- Inspect existing code before choosing a destination. Extend the owning capability instead of creating a parallel abstraction or duplicate implementation.
- Create files and directories only when the requested implementation needs them. The map below describes destinations, not a scaffold to generate in full.
- This skill decides structure. Python version, build backend, dependency tooling, execution model, and release workflow require their own decisions; do not infer them from the layout.

## Layout and ownership

```text
anyo/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── src/
│   └── anyo/
│       ├── __init__.py
│       ├── py.typed
│       ├── hooks.py
│       ├── application.py
│       ├── workflows.py
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── _contracts.py
│       │   ├── _worker.py
│       │   └── _memory.py
│       ├── agents.py
│       ├── scripts.py
│       ├── prompts.py
│       ├── evaluation.py
│       ├── optimization.py
│       ├── errors.py
│       └── integrations/
│           └── github/
│               ├── __init__.py
│               ├── _provider.py
│               ├── _configuration.py
│               └── _webhooks.py
├── tests/
├── examples/
├── docs/
│   ├── knowledge/
│   └── tasks/
└── skills/
```

| Location | Responsibility |
| --- | --- |
| `hooks.py` | Hook definitions, event and callback types, and provider validation/extension contracts. |
| `application.py` | Hook registration and validation, application lifecycle, provider registry, webhook HTTP transport, startup, and ASGI hosting. |
| `workflows.py` | Workflow definitions and composition; how developers describe work. |
| `execution/` | Shared queue contracts, durable identity/event transport, worker execution, admission, delivery/reply records, compatibility memory execution, and shutdown ownership. Keep public imports through `anyo.execution`. |
| `agents.py` | Agent definitions, configuration, and invocation contracts. |
| `scripts.py` | Contracts and support for user-provided scripts; the execution protocol remains a separate decision. |
| `prompts.py` | Prompt representations shared by agents, evaluation, and optimization. |
| `evaluation.py` | Evaluators, scores, and feedback. |
| `optimization.py` | Improving prompts using execution and evaluation results. |
| `errors.py` | Exception types shared across capabilities and exposed to consumers. |
| `integrations/<provider>/` | One package per concrete provider, owning its adapter, configuration, authentication, and event mapping; GitHub is the first example. |

Keep a capability's data types, configuration, validation, and private helpers with that capability. For example, evaluation results belong with evaluation and agent configuration belongs with agents. Avoid generic `models/`, `services/`, `core/`, or `utils/` collections that obscure ownership. Put an error used only within one capability there rather than in shared `errors.py`.

Keep package metadata and build configuration in `pyproject.toml`. Ship `py.typed` with the typed library so consumers' type checkers can use its annotations. Keep tests, examples, development skills, and project documentation outside the import package; check distribution contents when configuring packaging.

### Provider organization

- Put each concrete hook provider in its own `src/anyo/integrations/<provider>/` package, including its first implementation. Do not add new providers as loose modules in `integrations/`.
- Export the provider's public adapter from its `__init__.py`; keep existing imports stable when promoting a provider module to a package.
- Keep provider-specific configuration, authentication, payload mapping, and matching inside that package. GitHub separates these responsibilities into `_provider.py`, `_configuration.py`, and `_webhooks.py`; other providers add only the internal modules they need.
- Use `Provider` from `anyo.hooks` as the shared extension contract. Implement it structurally without requiring a common base class. Providers needing immediate responses can also implement `ResponseProvider`, as Slack does. Keep native event context and response validation in the provider package; the HTTP receiver and dispatchers must not acquire provider-specific branches.
- Keep tests for a provider under `tests/integrations/<provider>/` and test through its public package exports. Shared runtime tests stay with their owning capabilities.
- See the [provider contract](../../docs/knowledge/webhook-triggers.md#provider-packages-and-extension-contract) for the adapter responsibilities and registration pattern.

## Dependency boundaries

- Workflow definitions must not import the execution engine. Execution consumes definitions and invokes the relevant agent or script contracts.
- Define extension interfaces beside the capability that owns them. Concrete adapters in `integrations/` implement those interfaces; core capabilities must not import concrete provider modules.
- Hook contracts remain independent of HTTP hosting and execution. `application.py` composes those capabilities through provider contracts from `hooks.py`; `execution/` runs supplied callbacks through shared queue contracts without importing concrete providers or storage adapters. SQLite and Redis adapters live in `integrations/sqlite/` and `integrations/redis/`; the package root composes them, and the Redis SDK loads only when selected.
- Load an optional integration's dependencies when that integration is used. Importing `anyo` must work without optional providers installed.
- Optimization may depend on execution, evaluation, and prompts. Ordinary execution and evaluation must not require the optimizer.
- Keep shared prompt representations and shared errors independent of higher-level consumers. Avoid circular imports; move a contract to its actual owner instead of hiding a cycle with scattered deferred imports.
- Internal modules import the owning module directly rather than routing through the package root's public re-exports.

## Public imports and growth

- Use `anyo/__init__.py` for a small, deliberate set of public exports. Expose advanced public functionality through its capability module, such as `anyo.evaluation`.
- The package root may compose the default `Application` with concrete provider adapters to support `anyo.add_hook()` and `anyo.start()`. Keep this wiring at the composition root; it must not open sockets, resolve credentials, or start workers during import.
- Treat documented import paths as compatibility commitments. Keep private helpers beside their owner and prefix private names or modules with `_`. Use `__all__` to declare intended exports where useful; it does not enforce privacy.
- Keep imports free of operational side effects: importing the library must not initialize external clients, load models, execute workflows, or start background work.
- Start a capability as one module. Split it when it develops distinct responsibilities that benefit from separate files, not because a size threshold or template demands it.
- When promoting a module to a package, replace the module with a directory of the same name and preserve its public imports through `__init__.py`. For example:

```text
workflows.py  →  workflows/
                ├── __init__.py      # Existing public exports
                ├── _definition.py
                ├── _composition.py
                └── _validation.py
```

Create only the internal files the implementation needs. Do not keep both `workflows.py` and `workflows/`, or introduce additional distributions or namespace packages without a separate requirement.

## Tests, examples, and verification

- Put tests in `tests/`, named by capability, such as `test_workflows.py`. Add subdirectories and shared `conftest.py` fixtures only as needed.
- Exercise behavior through documented public imports. Use private imports only for focused internal tests; they do not establish a consumer API.
- Keep runnable examples in `examples/` and use the same installed-package imports consumers use. Do not modify `sys.path` or rely on importing source files directly.
- For module moves or export changes, verify affected public import paths and run relevant tests. For packaging changes, build the distribution, install the wheel in a clean environment, and smoke-test imports outside the source checkout; verify `py.typed` and required package resources are included. An editable install alone does not verify distribution contents.
- Review changed dependencies against the boundaries above. Update affected documentation using [anyo-docs](../anyo-docs/SKILL.md), and report any compatibility changes or checks that could not run.
