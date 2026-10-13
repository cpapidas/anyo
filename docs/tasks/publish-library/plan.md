# Publish Anyo as a Python library

## For Humans

Consumers should be able to install Anyo from PyPI and list it in `requirements.txt`. This task prepares MIT-licensed alpha releases after merges into `main` and records the evidence required before claiming publication.

Status: in-progress
Last updated: 2026-10-13
Scope: licensing, metadata, automatic alpha versioning, installed-artifact verification, GitHub Actions, release documentation, and PyPI publication.

## Release decisions

- The user requested autonomous publication on 2026-10-12, then selected the MIT license and automatic alpha publication on 2026-10-13.
- Retain distribution/import name `anyo`, setuptools, Python 3.12+, and the existing base dependencies and extras. Include the completed HTTP embedding work already merged into `main`.
- Trigger `.github/workflows/release.yml` on pushes to the actual default branch, `main`, including merged PRs. Publish only after the release checks pass.
- Use alpha series `0.1.0`, with `0.1.0aN` assigned from `github.run_number`. The workflow's `v0.1.0aN` tag on the triggering commit is authoritative; `setuptools-scm` resolves the dynamic package version. Do not create version-bump commits or require manual release dispatch.
- Failed runs may leave gaps. Re-running the original run retains its version and reuses its immutable distribution artifact once built. The workflow queues up to 100 pending runs; publication order may differ from run order, and the bounded queue does not guarantee publication for an unlimited burst of merges.
- Use PyPI Trusted Publishing with GitHub owner `cpapidas`, repository `anyo`, workflow filename `release.yml`, and environment `pypi`. The [release guide](../../wiki/releases.md) gives the exact one-time setup.
- Alpha APIs and durable storage formats may change. Consumers should pin a published version and upgrade processes sharing a queue together.

## Remaining dependencies

Publication has not occurred. The release PR must be merged into `main`, and the owner must register the pending Trusted Publisher in [PyPI account publishing](https://pypi.org/manage/account/publishing/). No authenticated PyPI account access is available to complete that setup. A pending publisher does not reserve the `anyo` name; only an accepted upload establishes publication.

The earlier GitHub workflow-upload block is resolved through the repository's working SSH remote. The GitHub `pypi` environment exists and restricts deployments to `main`. Active workflow files are included in the PR; the previous inactive patch is removed. No manual package tag or release dispatch is needed to start the new flow after merge.

## Completion criteria

- [x] The owner has selected MIT, and the versioning and publication policy are recorded.
- [x] Installation instructions distinguish pending publication, source/wheel installation, published version pins, optional extras, and external executables.
- [ ] Fresh source-to-wheel artifacts contain MIT license metadata/text, runtime resources, and typing metadata, with the expected tag-derived version.
- [ ] Current release scripts and workflow definitions pass their applicable checks, including rerun/version/artifact behavior.
- [ ] CI verifies installed artifacts on Linux/macOS and Python 3.12–3.15.
- [ ] The release PR is merged and the pending PyPI Trusted Publisher is configured.
- [ ] PyPI accepts the exact checked wheel and source archive under the intended account and version.
- [ ] A clean public-index installation and the published project page are verified.

## Current verification and outcome

The MIT and automatic-release changes require fresh verification. Record their exact checks, environments, artifact versions, and GitHub run results here. Earlier candidate results below do not establish that the changed release setup has passed. Publication remains unverified until the exact artifacts are accepted by PyPI and a clean public-index installation succeeds.

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

The [release guide](../../wiki/releases.md), [initial alpha release notes](release-notes.md), and [installation guide](../../wiki/installation.md) describe the current policy and remaining setup.

## Sources

- User conversation, 2026-10-12 (no share link): requested project context, publication requirements, confirmation of `requirements.txt` installation, and then autonomous publication.
- User follow-up, 2026-10-13 (no share link): requested a PR and use of authenticated GitHub access; subsequently chose MIT and automatic alpha releases for merges to the default branch, which is `main`.
- [Product direction](../../knowledge/product-overview.md): one distributable Python library and the selected alpha release policy.
- [Packaging skill](../../../skills/anyo-packaging/SKILL.md): artifact verification, license decisions, and publication-result confirmation.
