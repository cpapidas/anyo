# Run separate workers

## For Humans

A receiver accepts webhooks; workers run your pipelines. This example starts one receiver and two workers using the same application.

## Set up every process

Put the same application code on every machine. With a Python environment active, run from each checkout:

```sh
python -m pip install '.[redis]'
export ANYO_QUEUE_URL='redis://redis-host:6379/0'
export ANYO_QUEUE_NAMESPACE='github-reviews'
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
```

Use a running Redis service. Replace `redis-host`, `your-org`, and `your-repo`; use the same values in all three terminals.

These environment variables are read by [queued_webhook.py](../../examples/queued_webhook.py). In your own application, pass the settings to `anyo.start(...)`.

## Terminal 1: receiver

```sh
ANYO_ROLE=receiver \
ANYO_HOST=0.0.0.0 \
ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret' \
python examples/queued_webhook.py
```

[Connect GitHub](github-hooks.md#connect-github) to this receiver's public HTTPS URL, using the same signing secret.

## Terminal 2: first worker

```sh
ANYO_ROLE=worker python examples/queued_webhook.py
```

## Terminal 3: second worker

```sh
ANYO_ROLE=worker python examples/queued_webhook.py
```

Both workers keep checking the shared queue. Each job is claimed by one worker at a time.

- Workers connect to the queue, not the receiver's HTTP address.
- Workers need no webhook secret or listening port.
- Each worker needs its own prompt files, workspaces, and model credentials if its pipeline uses them.
- All processes must register the same hooks and stable `hook_id` values.

Each worker runs up to four pipelines at once: up to eight across these two workers. Add `workers=1` to the example's `anyo.start(...)` call to allow one per worker.

## Use SQLite on one machine

Install the base package with `python -m pip install .`. In each terminal, replace the Redis URL with the same absolute local file path:

```sh
export ANYO_QUEUE_URL='sqlite:////absolute/path/to/queue.sqlite3'
```

Choose a writable path, then use the same receiver and worker commands above. Do not share SQLite across machines or through a network filesystem.

For the original one-process setup, set `ANYO_ROLE=all` and run the same example with the webhook secret configured.

More detail: [hook identity and shared settings](../knowledge/worker-queues.md#identity-and-namespace-compatibility) and [worker lifecycle](../knowledge/worker-queues.md).

[All guides](README.md)
