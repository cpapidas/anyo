# Publish a release

## For Humans

Build and test the Python distribution before uploading it. The proposed GitHub workflow publishes the same wheel and source archive that passed its checks. Its files are supplied as an [inactive patch](../tasks/publish-library/workflows.patch), pending permission to install Actions workflows.

## First-publication setup

Publication is currently blocked on the project owner's license choice and PyPI account access. The available GitHub OAuth token also lacks the `workflow` scope required to push the workflow files. No package has been uploaded.

1. Select the license, add its text, and declare `project.license` and `project.license-files` in `pyproject.toml`. The release preflight rejects missing license metadata or files. To install the proposed workflows, refresh GitHub CLI authorization with `gh auth refresh -h github.com -s workflow`, complete its browser authorization, then apply the patch below and commit/push the resulting workflow files.
2. Sign in to the publishing account on PyPI with its required two-factor authentication.
3. Under account publishing, register a pending GitHub Trusted Publisher with project name `anyo`, owner `cpapidas`, repository `anyo`, workflow `release.yml`, and environment `pypi`.
4. To rehearse separately, configure the same publisher on TestPyPI using environment `testpypi`.

A pending publisher does not reserve the package name. Confirm name availability at publication. Do not store credentials in the repository or chat.

From the repository root, after workflow authorization:

```sh
git apply --check docs/tasks/publish-library/workflows.patch
git apply docs/tasks/publish-library/workflows.patch
```

Review and commit `.github/workflows/check.yml` and `.github/workflows/release.yml`. Keeping a patch in documentation does not install workflows or enable publication; the workflow files must be pushed through an authorized account before the commands below can work.

## Prepare the version

Update the single version in `pyproject.toml` and add consumer-facing release notes. Alpha releases use versions such as `0.1.0a1`; they do not promise a stable API or durable-storage compatibility between releases. Document migrations and unsupported combinations explicitly.

Run the [development checks](development.md). Once installed, the `Check library` workflow builds a source archive and its wheel, checks metadata, and runs installed-package tests on Linux and macOS with Python 3.12–3.15. Each environment first checks a base-only installation without optional clients or test dependencies, then adds extras and runs ordinary tests. Live service/model tests remain opt-in.

Before upload, inspect both archives for expected runtime resources, typing metadata, license files, and unwanted local files. This check complements the installed-package smoke.

## Publish a checked tag

Merge the verified release changes, then tag that commit with `v` followed by the exact project version. For the first alpha:

```sh
git tag v0.1.0a1
git push origin v0.1.0a1
gh workflow run release.yml --ref v0.1.0a1 -f index=testpypi
```

After the rehearsal succeeds, run the PyPI workflow against the same tag:

```sh
gh workflow run release.yml --ref v0.1.0a1 -f index=pypi
```

Each run builds and verifies its own artifacts from the tag before uploading. Publishing uses OIDC and a dedicated environment; it needs no long-lived PyPI token. The publication job has no source checkout and receives only the checked artifacts. Do not rerun an ambiguous upload until the index has been inspected; published filenames cannot be replaced.

## Verify the public result

In a fresh environment outside the checkout, install the exact version from PyPI:

```sh
python -m pip install 'anyo==0.1.0a1'
python -m pip check
python -c 'import anyo; from importlib.metadata import version; print(version("anyo"), anyo.__file__)'
```

Confirm the project page, version, artifact hashes, README rendering, and extras. Record the result in the release task, remove the pending-publication notes from the installation guide and README, and publish the matching GitHub release notes.

Sources: [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/), [publishing from GitHub Actions](https://docs.pypi.org/trusted-publishers/using-a-publisher/), and the repository [packaging skill](../../skills/anyo-packaging/SKILL.md).

[All guides](README.md)
