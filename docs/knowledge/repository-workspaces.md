# Repository workspace contract

## For Humans

Workers need isolated files without downloading the same repository for every job. This contract describes persistent Git caches, workspace ownership, and recovery.

Status: accepted  
Last updated: 2026-10-11  
Scope: `anyo.repositories`; Linux/macOS, Git 2.39+, local filesystems. SSH also requires the OpenSSH client, included in the root Docker image.

## Public API

Import `RepositoryCache`, `RepositoryCheckout`, `RepositoryError`, `HTTPSCredentials`, and `SSHCredentials` from `anyo.repositories`. The package root API is unchanged.

| Operation | Behavior |
| --- | --- |
| `checkout(...)` | Sync context manager; returns an absolute `path`, verified `commit_sha`, `cache_hit`, and `warnings`. |
| `acheckout(...)` | Async context manager; blocking Git/filesystem work runs off the event loop. |
| `maintain(timeout=120)` | Recover dead owners and remove idle/over-budget entries; returns `MaintenanceResult`. |
| `purge(url=..., timeout=30)` | Remove one inactive repository; returns whether it existed. Active users cause `cache_busy`. |
| `export_seed(destination, url=...)` | Atomically publish a new seed directory containing a full bundle and manifest. |
| `restore_seed(seed, url=...)` | Initialize an absent cache from a verified self-contained seed. |

Use either a trusted HTTPS/SSH `url` or explicit `local_path`. Acquisition requires either a full `commit` ID or a full `ref` such as `refs/heads/main`. A named ref refreshes every time; a complete cached pinned commit needs no remote contact.

`fetch_ref` helps retrieve a pinned commit, including `refs/pull/<number>/head`. Anyo verifies the requested SHA and never replaces it with a newer head. If the ref moved or disappeared, an exact fetch may recover the commit; otherwise acquisition fails.

## Defaults and limits

| Setting | Default |
| --- | --- |
| Cache directory | `.anyo/repositories`, made absolute at construction |
| Access namespace | `default` |
| Object format | `sha1`; explicitly select `sha256` for SHA-256 repositories |
| Preparation / seed / maintenance deadline | 120 seconds |
| Workspace cleanup deadline | 30 seconds; failure is logged and deferred to maintenance |
| Idle entry retention | 7 days, applied by `maintain()` |
| Maximum cache size | 5 GiB per namespace, including workspaces |
| Minimum free disk space | 64 MiB |

Git output is capped at 4 MiB per stream; excess output raises `output_limit`.

Size/free-space checks run before and after preparation; inactive entries are evicted in least-recently-used order. Git downloads and concurrently edited workspaces can temporarily exceed these soft bounds. Use filesystem quotas for hard limits. Retained commit refs remain until the whole entry is evicted or purged.

Git integrity checks detect damaged objects before cache reuse. An inactive damaged entry is quarantined and removed, then rebuilt once. Active damaged entries fail without replacing data under their users.

## Ownership and failure

Each repository has a bare cache, a mutation lock, and separate detached worktrees. Each workspace has a kernel-held lease for its whole context lifetime. Git subprocesses inherit the mutation/ownership descriptors and register their process groups with Anyo's existing execution supervisor.

Cancellation stops and reaps Git before unlocking. A killed worker cannot release a still-running Git child's inherited lock. Maintenance acquires abandoned workspace leases before deleting anything; it never uses PID, age, or queue lease expiry as proof that a user has stopped.

Normal context exit removes its workspace. Failed cleanup emits a warning and leaves a recoverable ownership record. `maintain()` or the next acquisition recovers abandoned data. Hold the context until all prompts and other file users finish; do not return its path for later use.

`RepositoryError.code` distinguishes `git_missing`, `git_version`, `unsupported_platform`, `authentication_failed`, `remote_unavailable`, `revision_unavailable`, `lock_timeout`, `timeout`, `cache_corrupt`, `cache_busy`, `storage_full`, `storage_unavailable`, and seed errors. Invalid configuration raises `ValueError`. Raw Git errors, credentials, and URLs are excluded from diagnostics. Repository preparation never retries consumer jobs or prompts.

## Credentials and trust

HTTPS uses `HTTPSCredentials(username, password, ca_file=None)`. The optional CA file supports a private trusted server. Credentials reach only the Git child environment and a non-persisting credential helper, never command arguments or cached remotes.

SSH uses `SSHCredentials(known_hosts=..., username="git", identity_file=... or agent_socket=...)`. Host checking stays enabled. Ambient SSH configuration and implicit identity files are disabled. Encrypted identities require an explicitly selected agent; interactive prompts are disabled.

Git runs with isolated configuration, hooks disabled, no checkout filters from global config, no submodule recursion, and only HTTPS/SSH or explicit local transport. URLs with credentials, unsupported protocols, query strings, fragments, and invalid refs are rejected. Acquisition secrets in inherited environment values are removed from model processes started inside the checkout context; caller environment variables are unchanged. Already-running or borrowed model servers remain caller-owned: do not give them repository credentials.

Cache hits do not revalidate remote access. Namespaces separate cache identities; filesystem permissions control access to private contents. Worktrees share Git objects/configuration and are for cooperating workers, not hostile tenants or an OS sandbox. Tools with unrestricted Git/filesystem access can change shared state.

Submodules stay uninitialized. LFS objects stay as pointer files. These produce `submodules_not_initialized` and `lfs_pointers_only` in `checkout.warnings`. No dependency installation, repository scripts, pushes, or posted reviews are performed.

## Deployment and seeds

Same-host processes and containers share an absolute cache path with compatible ownership. NFS and cross-host writable caches are unsupported. Redis does not transport Git data; use one local cache per host.

A seed is a full bundle plus an identity and SHA-256 manifest. Restore verifies a private copy, rejects missing prerequisites, and publishes only a complete cache. A digest checks integrity, not publisher trust: deploy seeds through a trusted channel. Seed identity includes the configured URL/path, namespace, schema, transport kind, and object format. No credentials or live worktrees are exported.

Follow the [runnable project-pulling examples](../../examples/repository-workspaces/README.md), [short setup guide](../wiki/repository-workspaces.md), and [deployment guide](../wiki/repository-deployment.md). The [implementation task](../tasks/repository-workspaces/plan.md) records verification.

## Sources

- User implementation authorization, 2026-10-11, following the repository workspace plan.
- [Repository implementation](../../src/anyo/repositories/__init__.py) and [behavior tests](../../tests/test_repositories.py).
- Git's [worktree](https://git-scm.com/docs/git-worktree), [fetch](https://git-scm.com/docs/git-fetch), and [bundle](https://git-scm.com/docs/git-bundle) contracts, checked 2026-10-11.
