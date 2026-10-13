# Publish a release

## For Humans

Merging into `main` starts an automatic alpha release. GitHub Actions builds and checks the distribution, then publishes the checked wheel and source archive to PyPI. Anyo uses the [MIT license](../../LICENSE).

The workflows are active on `main`. Version [0.1.0a2](https://pypi.org/project/anyo/0.1.0a2/) was the first successful publication after the macOS cleanup fix. The [release task](../tasks/publish-library/plan.md) records the release and clean-install verification.

## Publisher configuration

The PyPI project uses a GitHub Trusted Publisher with these values:

| Field | Value |
| --- | --- |
| PyPI project name | `anyo` |
| GitHub owner | `cpapidas` |
| Repository | `anyo` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The repository's `pypi` environment is configured to allow deployments from `main`. The publication job uses that environment and OIDC, so it needs no long-lived PyPI API token. The first successful upload created the project and converted its pending publisher to a normal publisher. [PyPI's pending publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

GitHub access through the repository's SSH remote works. The earlier workflow-permission issue is resolved; the [check workflow](../../.github/workflows/check.yml) and [release workflow](../../.github/workflows/release.yml) are merged.

## How versions are assigned

Every push to the default branch, `main`, including a merged PR, starts `release.yml`. The current alpha series is `0.1.0`. The workflow chooses `0.1.0aN`, where `N` is that workflow's `github.run_number`, and creates the matching `v0.1.0aN` tag on the triggering commit.

The tag is the authoritative release version. `setuptools-scm` resolves the package's dynamic version from Git metadata; `pyproject.toml` does not maintain a second version literal. No version-bump commit or manual tag is required. Building an untagged checkout can produce a development version, which is not a published alpha. [Version configuration](../../pyproject.toml) and [setuptools-scm usage](https://setuptools-scm.readthedocs.io/en/latest/usage/).

Failed runs can leave gaps in the alpha sequence. Re-running the original workflow run keeps its run number and therefore the same release version; a retry does not allocate another alpha. Run numbers reflect run creation, not upload completion order. [GitHub run context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context).

Keep the release workflow's run history. Replacing it with a new workflow requires reconciling its run counter with existing release tags; the tag guard rejects a version that already belongs to another commit.

The release workflow queues up to 100 pending runs. This is a bounded queue, so an unlimited burst of merges is not a guarantee of publication for every merge. Uploads can finish out of version order. Inspect failed or cancelled runs in Actions instead of assuming that a merge or tag means publication succeeded. [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## Checks before upload

Run the [development checks](development.md) before merging. The workflows build a source archive and its wheel, check metadata and archive contents, and run installed-package tests on Linux and macOS with Python 3.12–3.15. Each environment first checks a base-only installation without optional clients or test dependencies, then adds extras and runs ordinary tests. Live service/model tests remain opt-in.

The release checks include the MIT license, typing marker, dashboard assets, and prompt resources. During release preparation, also inspect the archives for unwanted local files. Publishing consumes the checked artifacts rather than building another copy. Alpha APIs and durable storage formats may change; describe migrations and unsupported combinations in the release notes.

After upload, the workflow verifies both remote file hashes. It uses a 60-second retry window for missing files to appear in the PyPI API, while invalid responses, network errors, unexpected files, and hash conflicts fail immediately.

## Recover a failed release

Open the original run in the repository's Actions page. Inspect the failing job and the PyPI project before retrying, especially when an upload's outcome is ambiguous.

Re-run that original run after resolving its blocker. Once the distribution artifact exists, a rerun reuses those immutable files for the same version. Do not replace published files, move a release tag, or rebuild a different distribution under the same release version. If the original artifact is unavailable, investigate before attempting publication. There is no `workflow_dispatch` release command.

After fixing the one-time publisher configuration, retry the original failed run to publish its checked artifact. A later merge starts a separate run and receives its own alpha number.

## Verify the public result

Use the version reported by the successful release run. The example below checks the first published alpha, `0.1.0a2`; use the release being verified and run it in a fresh environment outside the checkout:

```sh
python -m pip install 'anyo==0.1.0a2'
python -m pip check
python -c 'import anyo; from importlib.metadata import version; print(version("anyo"), anyo.__file__)'
```

Confirm the project page, version, artifact hashes, README rendering, and extras. The [release task](../tasks/publish-library/plan.md) records the first publication; record later consumer-visible changes in the matching GitHub release notes.

[All guides](README.md)
