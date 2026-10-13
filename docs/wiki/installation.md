# Install Anyo

## For Humans

Install Anyo in the same environment as your Python application. Python 3.12 or later is required.

Anyo is MIT-licensed. Automatic `0.1.0aN` alpha releases are prepared, but publication is still pending the release PR's merge and PyPI publisher setup. Use the source or wheel instructions until the [release task](../tasks/publish-library/plan.md) confirms publication.

## From source or a built wheel

From the repository checkout:

```sh
python -m pip install .
```

To install a verified wheel without the checkout, use its actual filename. This example uses `0.1.0a12` only to show the filename format:

```sh
python -m pip install /path/to/anyo-0.1.0a12-py3-none-any.whl
```

## From PyPI after publication

Put a verified published version in your application's `requirements.txt`. Replace the illustrative `0.1.0a12` below with the version you choose:

```text
anyo==0.1.0a12
```

Then install and check the package:

```sh
python -m pip install -r requirements.txt
python -c 'import anyo; print(anyo.__file__)'
```

The explicit version selects the alpha without needing `--pre`. Each successful release run publishes its own alpha; a later merge does not change the version pinned in your application. See [releases](releases.md#how-versions-are-assigned) for the numbering policy.

## Optional integrations

Use one requirement containing the features you need:

```text
anyo[redis,opencode]==0.1.0a12
```

From a checkout, the equivalent command is `python -m pip install '.[redis,opencode]'`.

The base package includes webhook integrations, SQLite storage, and the dashboard. The `redis` extra supplies the Python client; you also need a running Redis server. The `opencode` extra supplies the Python HTTP client; install the OpenCode executable separately. Native OmO needs its executable but no Python extra. The [prompt guide](run-prompts.md) explains the Docker option, which includes both executables.

Repository workspaces need Git 2.39 or later and Linux or macOS. Native managed OmO and process execution also require a POSIX platform; a pure-Python wheel does not make these features available on Windows.

Alpha APIs and durable storage formats may change. Pin your version, read release notes, and upgrade receivers and workers sharing a queue together.

[Quick start](../../README.md#try-it) · [All guides](README.md)
