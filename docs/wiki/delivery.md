# Understand deliveries and retries

## For Humans

A webhook response tells the sender whether Anyo accepted the event. Check the run itself to see whether your pipeline finished successfully.

## What happens to an event?

1. Anyo checks the signature and finds matching hooks.
2. It saves jobs and acknowledges the delivery.
3. Workers run each matching pipeline in the background.

For example, a GitHub `202` response means the job was accepted. The pipeline may still be waiting or running.

## What runs?

- Every matching hook runs independently. One failure does not stop the others.
- In default thread mode, `def` runs in a worker thread; `async def` runs on the event loop.
- Register all hooks before startup. Exact duplicate registrations are rejected.
- Bound your external calls with timeouts so a pipeline can finish.

## Can an event run twice?

SQLite and Redis keep delivery history across restarts. While that history is retained, queued, running, and successful jobs block duplicate deliveries for the same hook.

Failed or interrupted jobs can be explicitly retried. Automatic retries require a configured retry policy. Cancelled jobs need an explicit retry; redelivery alone does not restart them.

History expires, and a crash can leave work partly done. Make repeated effects safe: for example, update an existing PR comment instead of posting a second one on retry.

## What happens on shutdown?

Normal SQLite/Redis shutdown finishes running work and leaves waiting jobs for the next worker. Memory mode drains accepted work on normal shutdown, but cannot preserve it after a crash or restart.

Next: [inspect and retry in the dashboard](operations-ui.md), [HTTP response meanings](../../examples/README.md#delivery-responses-and-retries), or [retry and recovery details](../knowledge/worker-queues.md#admission-execution-and-recovery).

[All guides](README.md)
