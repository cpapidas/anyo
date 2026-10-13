# Install Anyo

## For Humans

Install Anyo in the same environment as your Python application. Python 3.12 or later is required.

The first alpha, `0.1.0a1`, is prepared for publication but is not yet on PyPI. Use the source or wheel instructions until the [release task](../tasks/publish-library/plan.md) confirms publication.

## From source or a built wheel

From the repository checkout:

```sh
python -m pip install .
```

To install a verified release wheel without the checkout:

```sh
python -m pip install /path/to/anyo-0.1.0a1-py3-none-any.whl
```

## From PyPI after publication

Put the published version in your application's `requirements.txt`:

```text
anyo==0.1.0a1
```

Then install and check the package:

```sh
python -m pip install -r requirements.txt
python -c 'import anyo; print(anyo.__file__)'
```

The explicit version selects the alpha without needing `--pre`.

## Optional integrations

Use one requirement containing the features you need:

```text
anyo[redis,opencode]==0.1.0a1
```

From a checkout, the equivalent command is `python -m pip install '.[redis,opencode]'`.

The base package includes webhook integrations, SQLite storage, and the dashboard. The `redis` extra supplies the Python client; you also need a running Redis server. The `opencode` extra supplies the Python HTTP client; install the OpenCode executable separately. Native OmO needs its executable but no Python extra. The [prompt guide](run-prompts.md) explains the Docker option, which includes both executables.

Repository workspaces need Git 2.39 or later and Linux or macOS. Native managed OmO and process execution also require a POSIX platform; a pure-Python wheel does not make these features available on Windows.

Alpha APIs and durable storage formats may change. Pin your version, read release notes, and upgrade receivers and workers sharing a queue together.

[Quick start](../../README.md#try-it) · [All guides](README.md)
