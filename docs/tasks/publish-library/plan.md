# Publish Anyo as a Python library

## For Humans

Consumers can install Anyo from PyPI and list it in `requirements.txt`. This task delivered MIT-licensed alpha releases after merges into `main` and records the first successful publication and installation checks.

Status: done
Last updated: 2026-10-13
Scope: licensing, metadata, automatic alpha versioning, installed-artifact verification, GitHub Actions, release documentation, PyPI publication, and the macOS Git-cleanup failure exposed by the first release run.

## Release decisions

- The user requested autonomous publication on 2026-10-12, then selected the MIT license and requested automatic publication on 2026-10-13. Retain the prepared alpha release line for this automation.
- Retain distribution/import name `anyo`, setuptools, Python 3.12+, and the existing base dependencies and extras. Include the completed HTTP embedding work already merged into `main`.
- Trigger `.github/workflows/release.yml` on pushes to the actual default branch, `main`, including merged PRs. Publish only after the release checks pass.
- Use alpha series `0.1.0`, with `0.1.0aN` assigned from `github.run_number`. The workflow's `v0.1.0aN` tag on the triggering commit is authoritative; `setuptools-scm` resolves the dynamic package version. Do not create version-bump commits or require manual release dispatch.
- Failed runs may leave gaps. Re-running the original run retains its version and reuses its immutable distribution artifact once built. The workflow queues up to 100 pending runs; publication order may differ from run order, and the bounded queue does not guarantee publication for an unlimited burst of merges.
- Use PyPI Trusted Publishing with GitHub owner `cpapidas`, repository `anyo`, workflow filename `release.yml`, and environment `pypi`. The [release guide](../../wiki/releases.md) gives the exact one-time setup.
- Alpha APIs and durable storage formats may change. Consumers should pin a published version and upgrade processes sharing a queue together.

## Publication outcome

Anyo [0.1.0a2 is published on PyPI](https://pypi.org/project/anyo/0.1.0a2/) and verified through clean public-index installations. [PR #4](https://github.com/cpapidas/anyo/pull/4) enabled automatic releases; the first attempt, `0.1.0a1`, stopped before upload. [PR #5](https://github.com/cpapidas/anyo/pull/5) supplied the macOS Git-cleanup fix before the first successful release. The owner configured the Trusted Publisher, and subsequent successful runs continue to publish automatically.

The earlier GitHub workflow-upload block is resolved through the repository's working SSH remote. The GitHub `pypi` environment exists and restricts deployments to `main`. Active workflow files are merged; the previous inactive patch is removed. No manual package tag or release dispatch is needed.

## Completion criteria

- [x] The owner has selected MIT, and the versioning and publication policy are recorded.
- [x] Installation instructions cover PyPI, source/wheel installation, published version pins, optional extras, and external executables.
- [x] Fresh source-to-wheel artifacts contain MIT license metadata/text, runtime resources, and typing metadata, with the expected tag-derived version.
- [x] Current release scripts and workflow definitions pass their applicable checks, including rerun/version/artifact behavior.
- [x] CI verifies installed artifacts on Linux/macOS and Python 3.12–3.15; every release must pass its own matrix before upload.
- [x] The release PR is merged.
- [x] The macOS Git-cleanup failure is repaired and the release's full check matrix passes.
- [x] The pending PyPI Trusted Publisher is confirmed configured by the owner.
- [x] PyPI accepts the exact checked wheel and source archive under the intended account and version.
- [x] A clean public-index installation and the published project page are verified.

## Current verification and outcome

The implementation merged in [PR #4](https://github.com/cpapidas/anyo/pull/4). Its preparation checks on 2026-10-13:

- An isolated build from a temporary Git clone tagged locally as `v0.1.0a1` produced a source archive and then a wheel from that archive. The verification tag was not pushed. Both artifacts passed strict Twine checks and the release metadata/resource guard, including matching MIT license text.
- All 64 wheel runtime/resource files matched source. The source archive included release scripts and tests; archive inspection found no local environment, credential, cache, or Git directories.
- A fresh base-only installation passed the resource and ASGI/SQLite lifecycle smoke from outside the checkout with `python -I`. Dependency checks passed before and after extras/test tooling were installed.
- **917 tests passed, with 41 live tests deselected**, against that installed wheel on macOS ARM64/Python 3.12 (48.08 seconds). This includes 39 release tests covering immutable version tags, full and partial upload retries, metadata validation, and conflicting remote hashes.
- Ruff lint and formatting passed. Mypy passed for 57 library modules and, separately, the three release scripts. The Docker `wheel` target built successfully without Git metadata using development version `0.0.dev0` and included the license.
- Actionlint 1.7.12 passed with only its unsupported `concurrency.queue` diagnostic excluded. `queue: max` is documented by GitHub; the installed validator predates support for that key.
- An [initial GitHub run](https://github.com/cpapidas/anyo/actions/runs/38345545322) exposed an intermittent macOS OmO process-cleanup failure. The [subsequent run](https://github.com/cpapidas/anyo/actions/runs/38345709784) passed build, lint, and all eight installed-package environments. The failure was still repaired rather than dismissed as a flaky check: Darwin can report `EPERM` while a process group contains only unreaped children. Cleanup now retries that response within its shutdown timeout and still raises persistent permission errors. All 41 focused OmO runtime tests passed, including six new deterministic regressions. The [PR checks](https://github.com/cpapidas/anyo/pull/4/checks) track validation of the final revision.
- Ten changed documentation openings and 151 local links/anchors passed inspection.

The [first automatic release run](https://github.com/cpapidas/anyo/actions/runs/38346668062) created `v0.1.0a1` and passed its build and metadata checks. Seven installed-package environments passed; a repository checkout test failed on macOS/Python 3.15, so publication was skipped.

The CI trace wraps an `OSError` as `storage_unavailable` without exposing its original errno. A deterministic Git-cleanup `EPERM` reproduction produces the same public failure during fallback from a missing ref to a commit SHA. The fix retries Darwin `EPERM` for at most one second while preserving persistent denials and other OS errors. All 58 repository tests passed (two live tests deselected), including five new regressions; Ruff, formatting, mypy, and diff checks passed. [PR #5](https://github.com/cpapidas/anyo/pull/5) carries the fix; final verification is recorded below. The original CI errno is not established by the reproduction.

After the Git-cleanup fix, **928 tests passed locally** against the installed wheel. [Attempt 1 of the `0.1.0a2` release](https://github.com/cpapidas/anyo/actions/runs/38348631686/attempts/1) passed build, lint, and all eight installed-package environments on Linux/macOS with Python 3.12–3.15. It uploaded the checked wheel and source archive through the `pypi` environment, but immediate verification did not yet see the files in the PyPI API.

[Attempt 2](https://github.com/cpapidas/anyo/actions/runs/38348631686/attempts/2) reran the failed jobs, reused the original artifacts, and verified matching hashes without another upload. It completed successfully and created the [GitHub release](https://github.com/cpapidas/anyo/releases/tag/v0.1.0a2). A follow-up workflow change gives verification a 60-second retry window for missing files; invalid responses, network errors, unexpected files, and hash conflicts still fail immediately.

A clean environment outside the checkout installed `anyo==0.1.0a2` from public PyPI. Base-only import, packaged resources, and ASGI/SQLite lifecycle checks passed before test or optional clients were installed. Installation with the `redis` and `opencode` extras and dependency consistency checks passed. The published project page, version metadata, and README rendering were checked. The `0.1.0a2` page retains its original prepublication notice; this documentation update corrects the source README and installation instructions. These results establish the first publication; later merges receive their own alpha versions.

## Earlier candidate verification

The following evidence was recorded on 2026-10-12 for the earlier manually versioned `0.1.0a1` candidate. Those artifacts predate MIT licensing and automatic versioning; they are historical verification evidence, not the artifacts intended for the updated release flow.

- An isolated `python -m build` produced `anyo-0.1.0a1.tar.gz`, then `anyo-0.1.0a1-py3-none-any.whl` from that source archive, under `dist/first-alpha/`. Strict Twine checks passed for both.
- All 64 runtime/resource files in the wheel matched the source. The typing marker, prompt envelope, OmO bridge, and dashboard assets were included. The source archive included tests and build inputs. Neither archive contained local environment, credential, cache, or Git directories. License files were absent because the owner had not yet chosen a license.
- Clean base-only installations passed outside the checkout with `python -I`, no optional/test clients present, packaged-resource reads, public hook registration, and the ASGI/SQLite startup/shutdown lifecycle. `pip check` passed before and after optional/test dependencies were installed.
- **878 tests passed and 41 live tests were deselected in each environment:** macOS ARM64/Python 3.12 (48.13 seconds), Linux ARM64/Python 3.12 (33.31 seconds), and Linux ARM64/Python 3.14 (33.27 seconds). Linux 3.12 used the declared base lower bounds, Starlette 1.7.0 and Uvicorn 0.53.0.
- Ruff lint and formatting passed; mypy passed for all 57 library modules. Two existing documentation code blocks were reformatted to satisfy the formatter.
- Actionlint passed for the earlier workflow definitions. Their publication preflight correctly rejected missing license metadata. The installed smoke used the documented package-level `anyo.lifespan()` helper.
- Markdown opening structure and 140 relative link targets were checked. The README logo source existed and root README links used absolute public URLs for PyPI rendering.
- PyPI and TestPyPI JSON project lookups returned HTTP 404 for `anyo` at that check. This was not a name reservation or proof that a later upload would be accepted.

Python 3.13/3.15, Windows, Redis service compatibility, and live provider/model calls were not exercised in that release verification. Temporary containers were removed automatically. The runtime implementation was unchanged. The earlier workflows could not be pushed through the available OAuth credential; this historical limitation is resolved by the current SSH access.

The [release guide](../../wiki/releases.md), [initial alpha release notes](release-notes.md), and [installation guide](../../wiki/installation.md) describe the publication policy and available installation methods.

## Sources

- User conversation, 2026-10-12 (no share link): requested project context, publication requirements, confirmation of `requirements.txt` installation, and then autonomous publication.
- User follow-up, 2026-10-13 (no share link): requested a PR and use of authenticated GitHub access; subsequently chose MIT and automatic alpha releases for merges to the default branch, which is `main`.
- [Product direction](../../knowledge/product-overview.md): one distributable Python library and the selected alpha release policy.
- [Packaging skill](../../../skills/anyo-packaging/SKILL.md): artifact verification, license decisions, and publication-result confirmation.
