# Reuse repository caches

## For Humans

Keep the cache when replacing a worker. On a new host, a bundle seed can supply Git data before the worker contacts the remote.

## Docker workers

The root Docker image includes Git. Build it and create a volume:

```sh
docker build -t anyo:local .
docker volume create anyo-repositories
```

Give the image's user access once:

```sh
docker run --rm --user root --entrypoint sh \
  -v anyo-repositories:/repositories anyo:local \
  -c 'chown 10001:10001 /repositories'
```

Run your own application with the volume mounted:

```sh
docker run --rm \
  -v anyo-repositories:/repositories \
  -v "$PWD/examples:/app:ro" \
  -e ANYO_REPOSITORY_CACHE=/repositories \
  -e REPOSITORY_URL -e REPOSITORY_COMMIT -e REPOSITORY_TOKEN \
  anyo:local python /app/repository_workspace.py
```

Set `REPOSITORY_URL` and `REPOSITORY_COMMIT` in your shell first. Set `REPOSITORY_TOKEN` only for a private repository. Run the command again: it creates a new container and reuses the cached commit.

Use the same mount path and UID for all workers sharing this volume. Keep cache storage on one host; network filesystems are unsupported.

## Seed a new host

After caching the repository, export a seed to a new directory:

```python
cache.export_seed("repository-seed", url=repository_url)
```

Copy that entire directory to the new host using your deployment tools. There:

```python
from anyo.repositories import RepositoryCache

cache = RepositoryCache("/repositories", namespace="review-app")
cache.restore_seed("repository-seed", url=repository_url)
```

Use the same URL, namespace, and object format on both hosts. Restore only into a cold cache. Anyo checks identity, checksum, and that the bundle needs no earlier Git data.

Seeds contain repository contents: transfer them through a trusted channel. Redis shares jobs, not Git files. A fresh host without a seed must download the repository.

## Clean up

```python
cache.maintain()  # Recover abandoned workspaces and evict idle data.
cache.purge(url=repository_url)  # Remove one repository after its users finish.
```

Active workspaces are protected by OS locks, even across containers. A queue lease timeout alone never permits deletion. To revoke cached access, stop its users and purge the cache and any seeds.

[Repository setup](repository-workspaces.md) · [All guides](README.md)
