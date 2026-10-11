# Organize hook providers into packages

## For Humans

Anyo will support several hook providers, each with different configuration and event formats. Keeping each provider in its own folder makes its responsibilities easy to find and extend. This task establishes that pattern for GitHub while retaining the existing shared runtime and consumer API.

Status: done

## Objective and scope

Promote `anyo.integrations.github` from a module to a package with a stable `GitHubProvider` export. Keep GitHub configuration, authentication, event translation, and matching within that package. Use the existing `Provider` protocol from `anyo.hooks` as the shared abstraction, and keep the receiver and dispatcher independent of concrete providers.

Related knowledge: [product direction](../../knowledge/product-overview.md) and [provider packages and extension contract](../../knowledge/webhook-triggers.md#provider-packages-and-extension-contract). Apply the [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), [packaging](../../../skills/anyo-packaging/SKILL.md), and [documentation](../../../skills/anyo-docs/SKILL.md) guidance.

## Implementation

- Create `src/anyo/integrations/github/` with a public `__init__.py` and private adapter, configuration, and webhook modules. Remove the old `github.py` module.
- Preserve `from anyo.integrations.github import GitHubProvider` and `anyo.add_hook(provider="github", ...)` without changing runtime behavior.
- Clarify provider contract responsibilities in code and document the pattern for future providers. Keep registration explicit; no new provider discovery mechanism or dependency is needed.
- Group GitHub tests under `tests/integrations/github/`. Reuse behavioral coverage rather than testing private filenames.
- Include nested provider tests in source distributions through `MANIFEST.in`; keep tests out of the runtime wheel.

## Completion criteria

- GitHub has its own package and all existing public imports and behavior pass their contract tests.
- Configuration validation and webhook translation remain provider-owned; shared runtime modules have no concrete provider imports or branches.
- The fake provider still works through the shared interface, including a consumer typing check.
- Lint, formatting, and type checks pass. A fresh source-to-wheel build includes the new package and typing marker, excludes the old module, and passes installed-package tests outside the checkout.
- Documentation and structure guidance describe the final organization with valid links.

## Outcome and verification

GitHub now lives in `src/anyo/integrations/github/`, with private modules for configuration, webhook handling, and the provider adapter. Its public import and all hook registration behavior are unchanged. The `Provider` protocol now documents the responsibilities expected from each adapter, and the structure guidance establishes one package per provider. The receiver and dispatcher needed no provider-specific changes.

Verification on 2026-10-03 with Python 3.12.14:

- `.venv/bin/python -m pytest -q`: 147 passed, including GitHub behavior, a consumer-defined fake provider, and the real HTTP server lifecycle.
- Ruff lint and formatting checks passed for source, tests, and examples. `.venv/bin/mypy` passed for all nine checked source files; a separate strict consumer check confirmed the existing fake provider conforms to `Provider`.
- `.venv/bin/python -m build --outdir dist/provider-packages-verified`: built a source archive and then its wheel. Twine metadata checks passed. The first build inspection exposed omitted nested tests; `MANIFEST.in` corrects that for the verified build.
- Both final archives include the new GitHub package and exclude the old `github.py` module. The source archive includes the relocated tests; the wheel contains only runtime package files and metadata, including `py.typed`.
- Installed the wheel into a fresh virtual environment outside the checkout. Public imports, configuration validation, authenticated event decoding, and hook registration passed with runtime dependencies alone. Dependency consistency passed. After adding test tools, all 147 tests extracted from the source archive passed against the installed wheel.
- Checked Markdown openings, 93 local links, and four Python examples across 16 documents.

Verified artifacts are under `dist/provider-packages-verified/`. No runtime dependency, public API, or package version changed, and nothing was published. Live GitHub calls and additional Python versions were not needed for this structural refactor and were not exercised.
