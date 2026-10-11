# Choose a queue

## For Humans

A queue holds jobs until your Python function can run. Start with SQLite; use Redis when workers need to run on different machines.

## Start with one process

After registering your hooks, use:

```python
anyo.start()
```

This receives webhooks and runs pipelines in one process. It stores jobs in `.anyo/queue.sqlite3`; no separate queue service or worker command is needed.

The default is four pipelines at a time per process and 100 running or queued jobs across the shared queue namespace.

To run only one pipeline at a time:

```python
anyo.start(workers=1)
```

Webhooks are still acknowledged before the pipeline finishes. There is no setting that makes the webhook response wait for the whole pipeline.

## Choose storage

Use one of these startup calls after registering your hooks:

```python
anyo.start(queue_url="sqlite:///.anyo/queue.sqlite3", queue_namespace="reviews")
```

```python
anyo.start(queue_url="redis://localhost:6379/0", queue_namespace="reviews")
```

For Redis, install the extra from the checkout and supply a running standalone Redis service, version 6.2 or later:

```sh
python -m pip install '.[redis]'
```

| Queue | Use it for |
| --- | --- |
| SQLite | One machine, with jobs saved in a local file. |
| Redis | Several machines sharing a queue service. |
| Memory | Disposable jobs in one process. |

A namespace names your application's jobs and delivery history. Use the same namespace for processes that work together.

Changing the queue URL does **not** move old jobs or history. Follow the [queue migration steps](../knowledge/worker-queues.md#cut-over-without-changing-pipelines).

## Use a temporary queue

To avoid a SQLite file:

```python
anyo.start(queue_url="memory://", workers=1)
```

Work still runs in the background. Jobs and delivery history disappear when the process stops.

The advanced `Application` constructor also defaults to memory unless you configure queue backends. See [Application and ASGI setup](../knowledge/worker-queues.md#advanced-application-composition-and-asgi).

Next: [run separate workers](separate-workers.md), [keep queue data in Docker](../knowledge/worker-queues.md#sqlite-and-docker-storage), or read [delivery behavior](delivery.md).

[All guides](README.md)
