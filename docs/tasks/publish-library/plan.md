# Publish Anyo as a Python library

## For Humans

Consumers should be able to install Anyo from PyPI and list it in `requirements.txt`. This task prepares the first alpha and records the evidence required before claiming publication.

Status: blocked
Last updated: 2026-10-13
Scope: metadata, release documentation, installed-artifact verification, CI, and PyPI publication.

## Release decisions and dependencies

- The user explicitly requested autonomous publication on 2026-10-12, following discussion of PyPI installation.
- Retain distribution/import name `anyo`, setuptools, Python 3.12+, and the existing base dependencies and extras.
- Prepare `0.1.0a1` as the first alpha, following the recommendation in the conversation. Keep the version authoritative in `pyproject.toml`.
- Include the completed HTTP embedding work already merged into `main`.
- Use GitHub Actions and PyPI Trusted Publishing. GitHub repository administration is accessible.
- License remains an owner decision. Neither the repository nor GitHub declares a license; no new license grant is inferred from the request to publish.
- PyPI publication access is unavailable: no configured publishing environment token, `.pypirc`, PyPI keyring token, GitHub Actions secret/environment, or signed-in PyPI browser session was found. No credentials were exposed.
- GitHub rejected the workflow push because the available OAuth token lacks `workflow` scope. The complete workflow implementation remains on the local `codex/publish-library` branch. The `codex/publish-library-pr` branch contains the release preparation plus the proposed workflows as an [inactive patch](workflows.patch), so the changes can be reviewed without installing Actions workflows. Applying and pushing that patch still requires workflow authorization. No package tag or GitHub release has been created.

## Completion criteria

- [ ] The owner has selected a license and the artifacts contain its declared text.
- [x] Version, project links, installation instructions, external requirements, and alpha compatibility policy are recorded.
- [x] Ruff, formatting, mypy, ordinary tests, isolated source-to-wheel build, metadata checks, archive inspection, base-only smoke, and dependency consistency pass in the environments below.
- [ ] CI verifies installed artifacts on the declared Python/platform combinations.
- [ ] PyPI accepts the exact checked wheel and source archive under the intended account and version.
- [ ] A clean public-index installation and the published project page are verified.

## Verification and outcome

Package publication has not occurred. PyPI and TestPyPI JSON project lookups both returned HTTP 404 for `anyo` at the final check; this does not guarantee that PyPI will accept the name.

Verification on 2026-10-12:

- An isolated `python -m build` produced `anyo-0.1.0a1.tar.gz`, then `anyo-0.1.0a1-py3-none-any.whl` from that source archive, under `dist/first-alpha/`. Strict Twine checks passed for both.
- All 64 runtime/resource files in the wheel match the source. The typing marker, prompt envelope, OmO bridge, and dashboard assets are included. The source archive includes tests and build inputs. Neither archive contains local environment, credential, cache, or Git directories. License files are absent pending the owner's decision.
- Clean base-only installations passed outside the checkout with `python -I`, no optional/test clients present, packaged-resource reads, public hook registration, and the ASGI/SQLite startup/shutdown lifecycle. `pip check` passed before and after optional/test dependencies were installed.
- **878 tests passed and 41 live tests were deselected in each environment:** macOS ARM64/Python 3.12 (48.13 seconds), Linux ARM64/Python 3.12 (33.31 seconds), and Linux ARM64/Python 3.14 (33.27 seconds). Linux 3.12 used the declared base lower bounds, Starlette 1.7.0 and Uvicorn 0.53.0.
- Ruff lint and formatting passed; mypy passed for all 57 library modules. Two existing documentation code blocks were reformatted to satisfy the current formatter.
- Actionlint passed for both workflows. The publication preflight correctly rejected missing license metadata. The installed smoke uses the documented package-level `anyo.lifespan()` helper.
- Checked Markdown opening structure and 140 relative link targets; the README logo source exists and root README links use absolute public URLs for PyPI rendering.

The proposed workflows target Linux/macOS and Python 3.12–3.15, but they are not installed and GitHub execution is unverified because the credential cannot push workflow files. Python 3.13/3.15, Windows, Redis service compatibility, and live provider/model calls were not exercised in this release task. Temporary verification containers were removed automatically. Runtime implementation was unchanged.

The [release guide](../../wiki/releases.md) records the exact authorization/publisher setup and remaining steps. [Candidate release notes](release-notes.md) and the [installation guide](../../wiki/installation.md) are prepared; they distinguish current source/wheel installation from pending PyPI availability.

## Sources

- User conversation, 2026-10-12 (no share link): requested project context, publication requirements, confirmation of `requirements.txt` installation, and then autonomous publication.
- User follow-up, 2026-10-13 (no share link): requested a PR and use of the authenticated `gh` command. GitHub repository access works, but workflow upload remains denied; the PR preserves proposed workflows as a non-executing patch for review.
- [Product direction](../../knowledge/product-overview.md): one distributable Python library and previously open release choices.
- [Packaging skill](../../../skills/anyo-packaging/SKILL.md): artifact verification, license decisions, and publication-result confirmation.
