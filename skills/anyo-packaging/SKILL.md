---
name: anyo-packaging
description: Configure and verify Anyo's Python distribution and prepare requested releases. Use for pyproject metadata, dependency constraints and extras, package resources, wheel and source builds, installation checks, versioning, and publishing workflows.
---

# Anyo packaging and releases

## For Humans

A library can work in its source checkout and still fail when another developer installs it. This skill checks Anyo's metadata, dependencies, and built artifacts so the delivered package supports the consumer behavior it promises.

## Establish the packaging context

- Read the root [README](../../README.md), applicable `AGENTS.md` files, [product direction](../../docs/knowledge/product-overview.md), and existing build, dependency, and CI configuration. Follow [anyo-structure](../anyo-structure/SKILL.md) for the single `src/anyo/` import package.
- Reuse the selected tools and documented commands. Python baseline, build backend, dependency manager, distribution name on an index, license, and release destination must come from actual project decisions; do not fill them with values copied from a tutorial.
- If initial packaging setup is requested, resolve needed choices in that work and record them. Do not introduce a second build backend or dependency workflow to accomplish a routine metadata change.
- Distinguish a local build, release preparation, and requested publication. Complete the scope the user requested using existing authorization; this skill does not itself request publishing or tagging.

## Describe the installable library

- Keep standard metadata in `pyproject.toml`: the chosen distribution name, version source, supported Python range, description, README, license, dependencies, and relevant project links. Declare the build backend and its requirements separately from runtime dependencies.
- Keep one authoritative version source. Do not maintain independent version literals in metadata, package code, and release scripts. Check metadata in the built artifact rather than assuming dynamic versioning resolved correctly.
- Declare direct runtime dependencies that the base library actually needs. Keep development tools out of runtime requirements, and put optional provider dependencies in documented extras.
- Treat dependency constraints as promises to consumers. Support lower bounds with compatibility checks; add upper bounds for a known constraint rather than guessing that every future version will break. Do not copy a fully pinned developer environment into library dependency metadata.
- Keep environment locks or development dependency groups separate from published runtime requirements. Use the selected tool's supported mechanism rather than maintaining several competing lists.
- Align `requires-python`, syntax, standard-library usage, dependency support, and the test matrix. Changing the minimum Python version or removing an extra is a consumer compatibility change.

Use [PyPA's pyproject guidance](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) for field semantics and the chosen backend's documentation for package discovery and resource inclusion.

## Preserve optional integrations and resources

- A base installation must import `anyo` and exercise core behavior without optional SDKs or credentials. Installing one provider extra must not require unrelated providers.
- Missing optional dependencies should fail when their integration is used and identify the actual extra to install. If translating `ModuleNotFoundError`, check the missing module's identity so a broken transitive import is not mislabeled as an absent extra.
- Include `py.typed` and runtime resources actually used by the package. Access installed resources through package-aware APIs rather than assuming the source checkout or process working directory is available.
- Inspect wheel and source archive contents separately. Wheels should contain runtime files and required metadata/license material; source archives must include what is needed to rebuild them. Do not blanket-exclude README or license files that the build needs, or unintentionally ship local secrets and development artifacts.

## Verify built artifacts

For packaging changes or release preparation, use fresh task-specific output and environment locations so old artifacts cannot be mistaken for the new build:

1. Build a source distribution and build a wheel from that source distribution using the configured frontend and isolated build dependencies. This checks that the source archive contains the required build inputs. If the workflow also produces a wheel directly from the checkout, verify the exact artifact intended for release as well.
2. Inspect distribution metadata, version, dependency declarations, file contents, and typing marker. Run the configured metadata checks.
3. Install the wheel into a clean environment with its declared base dependencies. From outside the checkout, with source-path overrides and editable installs absent, verify that imports resolve to the installed package. Exercise a small core public API example and any relevant resource loading.
4. Check dependency consistency and run applicable consumer-contract tests against that installation. Keep test tooling separate from a base-dependency smoke check so extra test dependencies cannot mask missing runtime requirements.
5. For changed extras, verify base behavior without them and installation/import behavior with the relevant extra. Use fake transports for adapter behavior; live provider checks follow [anyo-testing](../anyo-testing/SKILL.md).
6. Check the affected supported Python/dependency combinations through available local or CI jobs. State explicitly which combinations were not exercised.

Use the existing task commands when present. For a workflow using PyPA `build`, its default `python -m build` builds a source archive and then a wheel from that archive; selecting both `--sdist` and `--wheel` builds both directly from the source tree. See the [build CLI documentation](https://build.pypa.io/en/stable/reference/cli.html) before changing that behavior.

## Prepare or publish a release

- Follow the agreed versioning policy and describe consumer-visible changes, migrations, and deprecations. Do not invent a compatibility guarantee or release cadence; use [anyo-python](../anyo-python/SKILL.md) to assess API changes and [anyo-docs](../anyo-docs/SKILL.md) for release documentation.
- For requested publication, verify the intended version and destination and publish the exact checked artifacts through the configured workflow. Resolve first-publication name availability before attempting an upload; a local import name does not establish ownership of an index name.
- Confirm the publication result. If an upload result is ambiguous, inspect the destination before retrying; do not automatically bump a version or repeat release side effects to work around uncertainty.
- Report built artifact locations, checks performed, compatibility limitations, and whether publication was requested and completed. Keep credentials in the established authentication mechanism, outside source files and logs.
