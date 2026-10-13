# Publish a release

## For Humans

Merging into `main` starts an automatic alpha release. GitHub Actions builds and checks the distribution, then publishes the checked wheel and source archive to PyPI. Anyo uses the [MIT license](../../LICENSE).

The workflow files are part of the release PR. Publication is still pending its merge and the PyPI setup below; no package has been uploaded.

## Set up PyPI once

Sign in to the intended PyPI owner account and open [account publishing](https://pypi.org/manage/account/publishing/). Add a pending GitHub Trusted Publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `anyo` |
| GitHub owner | `cpapidas` |
| Repository | `anyo` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The repository's `pypi` environment is configured to allow deployments from `main`. The publication job uses that environment and OIDC, so it needs no long-lived PyPI API token. A pending publisher creates the project on its first successful upload; it does not reserve the name. This setup requires access to the owner's PyPI account, which is not available in the current release task. [PyPI's pending publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

GitHub access through the repository's SSH remote works. The earlier workflow-permission issue is resolved; use the actual [check workflow](../../.github/workflows/check.yml) and [release workflow](../../.github/workflows/release.yml) included in the PR.

## How versions are assigned

Every push to the default branch, `main`, including a merged PR, starts `release.yml`. The current alpha series is `0.1.0`. The workflow chooses `0.1.0aN`, where `N` is that workflow's `github.run_number`, and creates the matching `v0.1.0aN` tag on the triggering commit.

The tag is the authoritative release version. `setuptools-scm` resolves the package's dynamic version from Git metadata; `pyproject.toml` does not maintain a second version literal. No version-bump commit or manual tag is required. Building an untagged checkout can produce a development version, which is not a published alpha. [Version configuration](../../pyproject.toml) and [setuptools-scm usage](https://setuptools-scm.readthedocs.io/en/latest/usage/).

Failed runs can leave gaps in the alpha sequence. Re-running the original workflow run keeps its run number and therefore the same release version; a retry does not allocate another alpha. Run numbers reflect run creation, not upload completion order. [GitHub run context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context).

The release workflow queues up to 100 pending runs. This is a bounded queue, so an unlimited burst of merges is not a guarantee of publication for every merge. Uploads can finish out of version order. Inspect failed or cancelled runs in Actions instead of assuming that a merge or tag means publication succeeded. [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## Checks before upload

Run the [development checks](development.md) before merging. The workflows build a source archive and its wheel, check metadata and archive contents, and run installed-package tests on Linux and macOS with Python 3.12–3.15. Each environment first checks a base-only installation without optional clients or test dependencies, then adds extras and runs ordinary tests. Live service/model tests remain opt-in.

The release checks include the MIT license, typing marker, dashboard assets, and prompt resources. During release preparation, also inspect the archives for unwanted local files. Publishing consumes the checked artifacts rather than building another copy. Alpha APIs and durable storage formats may change; describe migrations and unsupported combinations in the release notes.

## Recover a failed release

Open the original run in the repository's Actions page. Inspect the failing job and the PyPI project before retrying, especially when an upload's outcome is ambiguous.

Re-run that original run after resolving its blocker. Once the distribution artifact exists, a rerun reuses those immutable files for the same version. Do not replace published files, move a release tag, or rebuild a different distribution under the same release version. If the original artifact is unavailable, investigate before attempting publication. There is no `workflow_dispatch` release command.

After fixing the one-time publisher configuration, retry the original failed run to publish its checked artifact. A later merge starts a separate run and receives its own alpha number.

## Verify the public result

Use the version reported by the successful release run. The example below uses `0.1.0a12` only as an illustration; replace it with the actual published version and run it in a fresh environment outside the checkout:

```sh
python -m pip install 'anyo==0.1.0a12'
python -m pip check
python -c 'import anyo; from importlib.metadata import version; print(version("anyo"), anyo.__file__)'
```

Confirm the project page, version, artifact hashes, README rendering, and extras. Record the result in the [release task](../tasks/publish-library/plan.md) and remove pending-publication notes only after the public installation succeeds.

[All guides](README.md)
