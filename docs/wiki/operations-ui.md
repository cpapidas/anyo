# Open the dashboard

## For Humans

See pipeline runs and logs in your browser. Use the dashboard to retry failures, cancel work, and control workers.

## Try the example

On Linux or macOS, [install Anyo](../../README.md#try-it), then run from the checkout:

```sh
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
export ANYO_UI_USERNAME='operator'
export ANYO_UI_PASSWORD='replace-with-a-separate-long-random-password'
python examples/operations_ui.py
```

Open [the dashboard](http://127.0.0.1:8000/ui/) and sign in with those UI credentials.

[Connect GitHub](github-hooks.md#connect-github), then open a PR. The example runs for ten seconds and prints progress. To try it without GitHub, send the [local test event](../knowledge/operations-ui.md#start-a-local-dashboard).

## Add it to your application

Set `ANYO_UI_USERNAME` and `ANYO_UI_PASSWORD`. In the [README example](../../README.md#try-it), replace the startup call with:

```python
anyo.start(ui=True, execution_mode="process")
```

Process mode lets you force-stop an individual run. It requires Linux/macOS, an importable top-level pipeline function, and the `if __name__ == "__main__":` guard shown in the example.

Use `execution_mode="thread"` for the default execution mode. A stuck synchronous thread cannot be force-stopped individually.

The dashboard needs SQLite or Redis and is off by default. Use HTTPS when accessing it remotely.

## Use separate workers

Use the same hooks, queue URL, and namespace in each process. After registration, the receiver uses:

```python
anyo.start(role="receiver", ui=True)
```

Each worker uses:

```python
anyo.start(role="worker", operations=True, execution_mode="process")
```

Only the receiver needs UI and webhook credentials. The [operations example](../../examples/operations_ui.py) selects these settings from `ANYO_ROLE`.

More detail: [logs and retention limits](../knowledge/operations-ui.md#log-coverage-and-handling), [process restrictions](../knowledge/operations-ui.md#choose-an-execution-mode), and [upgrading an existing queue](../knowledge/operations-ui.md#upgrade-an-existing-queue).

[All guides](README.md)
