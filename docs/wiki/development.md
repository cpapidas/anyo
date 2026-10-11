# Develop Anyo

## For Humans

Use these commands when changing Anyo itself. Run them from the checkout with Python 3.12 or later.

## Install development tools

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Check a change

```sh
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Default tests use local fixtures and fakes. They need no model credentials, paid calls, or live services. Development dependencies include `httpx2` for Starlette's test client.

Live tests are opt-in. After preparing their required services and credentials, select them with `python -m pytest -m live`.

## Check a build

```sh
python -m build
python -m twine check dist/*
```

The build creates a source archive, then a wheel from that archive. Also install and verify the wheel in a clean environment outside the checkout; see the [packaging guide](../../skills/anyo-packaging/SKILL.md).

## Find project guidance

| Work | Read |
| --- | --- |
| Project rules | [AGENTS.md](../../AGENTS.md) |
| Python layout and imports | [Package structure](../../skills/anyo-structure/SKILL.md) |
| Public APIs and implementation | [Python development](../../skills/anyo-python/SKILL.md) |
| Behavior tests | [Testing](../../skills/anyo-testing/SKILL.md) |
| Dependencies and releases | [Packaging](../../skills/anyo-packaging/SKILL.md) |
| Writing docs | [Documentation](../../skills/anyo-docs/SKILL.md) |

Keep the root README short. Put simple how-to guides in `docs/wiki/`, detailed contracts and decisions in `docs/knowledge/`, and task history in `docs/tasks/<task-name>/plan.md`.

Start Markdown documents with a title and `For Humans`. Keep explanations short, include examples, and link from the [documentation index](../README.md).

[All guides](README.md)
