# Anyo

## For Humans

<p align="center">
  <img src="doc/asserts/anyo-logo.png" alt="Anyo logo" width="360">
</p>

With Anyo, you can ask your AI model to build workflows such as:

- **A PR reviewer** that checks new pull requests.
- **A bug analyzer** that investigates bug reports shared in Slack.
- **A team assistant** that answers questions in Slack.

Anyo handles the integrations and job execution, so your jobs stay clean and easy to read.

We support GitHub, Slack, and Better Stack today. We plan to add more tools soon.

## Example: build a PR reviewer

Ask your AI model:

> Use Anyo to build a PR reviewer. When a pull request opens, review its changes and summarize bugs and suggested fixes.

After you connect GitHub and configure your AI tool, the workflow is:

**New pull request → Anyo starts your job → AI reviews the changes → Your code gets the review.**

Your job supplies the PR changes and review prompt. Anyo handles the webhook, queue, and AI execution, so your Python code stays focused on the review.

## Try it

Use Python 3.12 or later. From this checkout:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Anyo is installed from source; this project has no published package yet.

Save this as `main.py`. Replace `your-org` and `your-repo` with your repository owner and name:

```python
import anyo
from anyo import HookEvent


def pipeline(event: HookEvent) -> None:
    print(f"New pull request: #{event.pull_request_number}")


if __name__ == "__main__":
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization="your-org",
        repository="your-repo",
        pipeline=pipeline,
        hook_id="new-pull-request",
    )
    anyo.start()
```

Start it with your webhook secret:

```sh
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python main.py
```

[Connect GitHub](docs/wiki/github-hooks.md#connect-github), then open a pull request. Your function prints its number.

Anyo listens on `127.0.0.1:8000` and saves jobs in `.anyo/queue.sqlite3`. Everything runs in one process by default.

## How-to guides

- [Connect GitHub](docs/wiki/github-hooks.md), [Slack](docs/wiki/slack-hooks.md), or [Better Stack](docs/wiki/betterstack-hooks.md).
- [Run an AI prompt](docs/wiki/run-prompts.md) or [prepare repository files](docs/wiki/repository-workspaces.md).
- [Choose SQLite or Redis](docs/wiki/workers.md) or [run separate workers](docs/wiki/separate-workers.md).
- [Open the dashboard](docs/wiki/operations-ui.md).
- [Understand deliveries and retries](docs/wiki/delivery.md).
- [Develop Anyo](docs/wiki/development.md).

[All guides](docs/wiki/README.md) · [Project status](docs/wiki/project.md) · [Runnable examples](examples/README.md)
