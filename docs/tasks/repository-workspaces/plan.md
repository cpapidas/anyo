# Clone repositories and reuse cached Git data across workers

## For Humans

A pipeline needs repository files before an agent can review a pull request or work on related code. Downloading the same history whenever a worker starts wastes time, while sharing one writable checkout lets concurrent runs interfere with each other. This task records the delivered persistent cache, separate workspaces, runnable examples, and verification, alongside the original design proposal.

Status: done  
Created: 2026-10-11  
Last updated: 2026-10-11  
Scope: repository acquisition, exact-commit workspaces, persistent cache reuse, worker lifecycle integration, deployment examples, and verification.

## Objective and authorization

Let users prepare their repositories from Python and pass a ready workspace to a pipeline or prompt. Reuse downloaded Git data across worker starts and job attempts, fetching missing changes when needed instead of downloading a full repository for each worker.

The user requested this task plan on 2026-10-11, initially for planning alone, then authorized implementation and requested runnable project-pulling examples. The design sections preserve the original proposal; the [final contract](../../knowledge/repository-workspaces.md), implementation outcome, and verification below describe the delivered behavior.

Related knowledge: [product direction](../../knowledge/product-overview.md), [worker queues](../../knowledge/worker-queues.md), [webhook contract](../../knowledge/webhook-triggers.md), [OpenCode prompts](../../knowledge/opencode-prompts.md), [native OmO prompts](../../knowledge/omo-prompts.md), and [operations controls](../../knowledge/operations-ui.md).

Dependencies: the completed [worker task](../sqlite-redis-workers/plan.md) supplies execution and retries; the completed [operations task](../protected-operations-ui/plan.md) supplies process ownership and cancellation. Follow the repository's [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), and [documentation](../../../skills/anyo-docs/SKILL.md) guidance during implementation, with [packaging guidance](../../../skills/anyo-packaging/SKILL.md) for distribution verification.

## Current foundation

- GitHub hooks provide the target repository, pull-request number, immutable `head_sha`, and payload. Receiving a webhook does not clone a repository. A fork PR's head commit need not be on an ordinary branch of the target repository.
- Prompt calls already accept an existing `workspace` directory. Both harnesses can use a prepared checkout without changing the prompt or result contract.
- SQLite workers share a local queue file; Redis workers can run on different machines. Queue storage transports event data, not repository files or receiver-local paths.
- Thread execution is the default. Process execution starts a fresh interpreter for each attempt, so an in-memory cache or open parent client cannot provide persistent repository reuse.
- Retries preserve the admitted event, and an expired queue lease does not prove the old execution has stopped. Repository cleanup must respect those constraints.

## Proposed consumer experience

Provide a repository capability usable inside callbacks and independently of the worker runtime. A context manager returns an absolute workspace path and its verified commit SHA; exiting releases that workspace while keeping reusable Git data. Construction is inert, and the library never changes the process working directory.

The consumer pattern below is implemented. See the [final contract](../../knowledge/repository-workspaces.md) for defaults and limits.

```python
import anyo
from anyo.repositories import RepositoryCache


def review_pull_request(event):
    if event.head_sha is None or event.pull_request_number is None:
        raise ValueError("A pull-request commit and number are required")

    cache = RepositoryCache(
        directory="/state/anyo/repositories",
        namespace="review-app",
    )
    with cache.checkout(
        url="https://github.com/your-org/your-repo.git",
        commit=event.head_sha,
        fetch_ref=f"refs/pull/{event.pull_request_number}/head",
        timeout=120,
    ) as checkout:
        result = anyo.inject_prompt(
            "review.md",
            workspace=checkout.path,
            context={"head_sha": checkout.commit_sha},
        )
        result.raise_for_failure()
```

The repository URL comes from trusted consumer configuration. The PR ref helps retrieve objects; `commit` remains the authoritative revision even if that ref has moved. The implementation uses a 120-second preparation deadline by default. Authentication is supplied separately at runtime, never embedded in this URL.

Support an async acquisition context for async hooks without blocking the event loop. A checkout remains owned until all code and harness activity using it has finished. Multiple repositories use independent handles; if later preparation fails, already acquired workspaces must still be released. A new attempt always receives a new workspace, including when retrying the same SHA.

## Recommended cache design

Use a persistent bare Git repository per repository identity, then create a detached worktree for each acquisition. Git supports multiple worktrees sharing repository data, with a separate checkout and HEAD for each. This is the proposed use of that mechanism for Anyo. [Git worktree documentation](https://git-scm.com/docs/git-worktree).

| Resource | Lifetime and ownership |
| --- | --- |
| Bare repository cache | Shared by compatible workers with access to the same persistent cache root; survives callback, worker, and container restarts. Never used as the prompt workspace. |
| Detached worktree | Unique to one acquisition/attempt; contains writable files and its own index. Normally removed after its owner finishes. |
| Cache metadata and locks | Owned by the repository capability; coordinate population, fetches, active users, maintenance, and cleanup across processes. |
| Optional bundle seed | Immutable Git data, without acquisition credentials or local configuration, used to initialize a cache on another machine; delivered by the deployment. |

Propose `.anyo/repositories` as the local default, resolved to an absolute path when used. Deployment examples should use an explicit writable persistent path. Keep repository data separate from queue files and ephemeral harness state, even when they live on the same volume.

Derive cache identity from the configured host/port, repository path, transport policy, cache schema/object format, and an explicit application/access scope. Do not key only by repository name, put credentials in keys, or merge distinct identities through unverified URL normalization. Namespace/access scopes separate private repositories belonging to different applications; they are not a substitute for filesystem permissions. Credential rotation within the same trusted scope should not force a full download.

### Acquisition and freshness

1. Validate configuration and inputs before invoking Git. Resolve the cache entry and acquire its process-safe coordination lock within the preparation deadline.
2. On a cold cache, populate a temporary sibling directory, validate it, and atomically publish it. Concurrent requests for the same repository wait and reuse the published result. Incomplete initial downloads are never treated as valid entries.
3. On a warm cache, reuse a requested full commit SHA when its required tree and objects are present. Otherwise fetch the explicitly requested branch, tag, PR ref, or commit as the remote permits. Fetch missing objects into the cache; do not delete and reclone merely because a revision is new.
4. Verify the result resolves to the requested commit before creating the workspace. A branch/tag request refreshes its ref by default and records the resolved SHA. A pinned request never silently switches to the latest branch or PR head if the original commit is unavailable.
5. Record active ownership, retain the required objects, and create a unique detached worktree. Release the repository mutation lock before running user code or a prompt. Keep the active-use protection until workspace release.
6. On completion, remove only the owned worktree and release its ownership record. Preserve the cache for subsequent jobs and newly spawned workers.

Use explicit fetch refspecs and keep Anyo's retained commit refs separate from remote refs. Git's atomic fetch option updates refs together; it does not replace Anyo's coordination across acquisition and cleanup. Avoid blanket mirror updates that could overwrite internal retention refs. [Git fetch documentation](https://git-scm.com/docs/git-fetch) and [clone mirror behavior](https://git-scm.com/docs/git-clone).

Prefer complete history for the selected refs in the first implementation so review tooling can inspect ancestors and compute merge bases. Shallow/partial fetches and sparse checkouts are later optimizations, requiring explicit completeness rules. If a workflow needs both base and head commits, acquire both explicitly; a head checkout alone does not define a PR diff.

For GitHub, support retrieving a PR head through the configured base repository's `refs/pull/<number>/head`, including fork PRs, then verify the event's exact SHA. GitHub documents fetching PR refs from that repository. A moved or deleted ref may no longer expose an older event SHA; use cached objects or an allowed exact fetch, otherwise report that revision as unavailable. Never follow arbitrary clone URLs from webhook payloads. [GitHub PR checkout guide](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/checking-out-pull-requests-locally).

### Reuse across deployment shapes

| Deployment | Proposed reuse contract |
| --- | --- |
| New threads, worker processes, or per-attempt child processes on one host | Reuse the same disk cache through interprocess coordination; no origin contact for a complete cached pinned commit. |
| Replacement or additional containers on one host | Mount the persistent cache and managed workspace directories at the same absolute paths with compatible ownership. Cache reuse survives container replacement. |
| Redis workers on different hosts | Each host uses a local persistent cache. Redis does not distribute Git objects. A new host can restore an optional bundle seed, then fetch only what that seed lacks. |
| Fresh host without a seed or persistent cache | An initial remote acquisition is unavoidable. Document this as a cold cache, without claiming that spawning a worker anywhere is network-free. |

Include a bounded export/restore workflow for self-contained Git bundles as the proposed seed mechanism. A full bundle can initialize an empty repository, while incremental bundles require prerequisite commits; the initial seed contract should accept self-contained bundles only. [Git bundle documentation](https://git-scm.com/docs/git-bundle).

Create a consistent seed under cache coordination, publish it atomically, and associate it with a trusted repository/access identity and integrity digest. Verify identity, digest, and bundle prerequisites before restoration; a checksum alone is not proof of origin. Seed import must not include acquisition credentials, worktrees, or runtime configuration, and must still validate the requested commit. Repository history itself may contain sensitive content, so seeds require the same access controls as the cache. Hosting, transport, and refreshing seeds remain deployment-owned; no cloud storage provider or new service is selected.

Do not assume a live Git directory on NFS or another cross-host writable filesystem has the required locking and path semantics. That deployment is outside the initial support target. Bundle seeding still transfers data to a new host; its benefit is reusing a prepared cache source and reducing origin downloads, not eliminating all transfer.

## Concurrency, cleanup, and failure behavior

- Use per-repository coordination across both threads and OS processes. Fetch, publication, worktree registration/removal, and maintenance must not race. Git's worktree lock protects worktree retention; it is not the application's fetch mutex. Different repositories should prepare concurrently.
- Keep the mutation lock separate from active-use protection. Maintenance cannot evict a cache entry, prune required objects, or remove a worktree while it is in use. Control automatic Git maintenance so it does not bypass these rules.
- Track unique acquisition IDs and verifiable local ownership. Queue lease expiry or a missing heartbeat alone is insufficient evidence for deletion. A resumed old attempt and its retry must never own the same workspace. Cross-container owner checks must account for different PID namespaces; a PID alone is insufficient.
- Cancellation and timeouts stop and reap owned Git processes before removing partial state or releasing mutation locks. Integrate their process groups with the existing supervisor. Graceful worker stop lets active users release normally; forced stop leaves recoverable records for later reconciliation. Preserve thread mode's existing limits on killing user callbacks.
- Recover abandoned temporary directories and worktree registrations only after proving their owners are gone. Do not steal locks or delete active state based only on age. If ownership is uncertain, retain the data and report it for inspection.
- Add configurable idle retention, cache size limits, and free-space checks. Evict inactive entries in least-recently-used order; fail new preparation clearly if space cannot be recovered without affecting active runs. Filesystem quotas remain the hard bound during unpredictable downloads. Debug retention of failed workspaces, if offered, is opt-in and bounded.
- Treat corruption separately from a missing revision or authentication error. Quarantine and rebuild only an inactive damaged entry, with a bounded repair attempt; never replace an entry underneath active users. Ordinary acquisition failure must not trigger an endless reclone loop.
- Raise distinguishable, sanitized failures for unavailable Git, invalid inputs, authentication, missing revision, lock timeout, network timeout, corruption, and insufficient storage. An async cancellation propagates after cleanup. Pipeline retries remain governed by the existing queue policy; repository preparation must not replay user code or prompts itself.

Cache hits do not prove that remote permissions are still valid. Cached private content is readable to the deployment's authorized local users until it is purged. Document revocation/purge procedures and the offline behavior of pinned cache hits separately from remote authentication failures.

## Authentication and workspace boundaries

Support public repositories and private repositories through explicitly configured HTTPS credentials or SSH authentication. Use Git as a bounded subprocess with argument lists, disabled interactive prompts, normal TLS/host-key verification, and controlled environment/configuration. Select the minimum supported Git version from the required commands and verify it on Linux and macOS; no new Python Git binding is proposed.

Reject credential-bearing URLs, unsafe protocols/remote helpers, option-like arguments, and invalid refs or destination paths. Local fixture repositories and internal cache paths use a separate trusted path from network URLs. Do not run repository hooks, recursively initialize submodules, or invoke checkout filters through uncontrolled ambient Git configuration. Permit only explicitly selected credential helpers and SSH settings; do not forward acquisition secrets to model processes or store them in remotes, seed artifacts, logs, or queue payloads.

Submodule content and Git LFS objects are excluded from initial materialization. Detect and report their presence: submodules remain uninitialized and LFS files remain pointers, rather than being represented as complete source contents. Automatic dependency installation, repository scripts, pushing commits, and posting reviews are outside this task.

Worktrees separate working files and indexes but still share refs, objects, and some configuration. They are suitable for cooperating workers in one trusted deployment, not an OS sandbox or tenant boundary. Tools with unrestricted filesystem/Git access could modify common state; document that limit and avoid presenting detached HEAD as protection against it. [Git worktree sharing rules](https://git-scm.com/docs/git-worktree).

If independent Git metadata is needed, consider local clones with copied objects or reference-assisted clones followed by dissociation. Persistent alternates created by `--shared` or `--reference` can break when their source prunes objects, so they are not the recommended default. Ordinary local clone optimization also needs coordination with source mutation. These are alternatives to evaluate, not additional backends required by this plan. [Git clone documentation](https://git-scm.com/docs/git-clone).

## Component boundaries and delivery steps

1. **Finalize the contract.** Confirm public input/result/error types, sync/async ownership, access scopes, freshness behavior, supported Git versions, and retention/deadline defaults. Start the capability in `src/anyo/repositories.py`, growing it only as responsibilities require. Keep GitHub PR mapping in its integration or the consumer example; the generic cache must not import a concrete hook provider.
2. **Build preparation and reuse.** Implement validated Git invocation, atomic cache population, incremental fetch, exact-commit verification, detached workspaces, process-safe locks, and lifecycle cleanup. Keep the capability usable outside queues; do not put Git data or clients in SQLite/Redis job envelopes.
3. **Connect lifecycle and diagnostics.** Use the execution-owned process supervision and existing observability contracts. Record safe cache hit/miss, fetch, resolved SHA, preparation duration, lock wait, and cleanup outcome with job/attempt context where available. Existing operations logs are sufficient; a new cache dashboard is not required.
4. **Cover persistent deployment and seeds.** Provide a shared local volume example, a replacement-worker example, and bundle export/restore for a fresh host. Document consistent mount paths, UID permissions, private cache access, cold-cache behavior, and seed transport ownership.
5. **Verify and document.** Add deterministic fixtures and consumer examples, run the relevant checks, and update the knowledge/wiki guides to the delivered behavior. Preserve existing hook, prompt, queue, and cancellation APIs. No package publication or production deployment is part of this task.

## Verifiable completion criteria

- [x] A consumer can acquire public or authenticated private repository content at an exact requested commit and pass its workspace to either harness. The configured clone URL and credentials remain separate from webhook authentication.
- [x] After one successful preparation, terminate that worker and start a fresh process against the same storage with origin access disabled. The same commit succeeds without a remote clone or fetch. Verify container replacement against a persistent volume too.
- [x] Concurrent first requests publish one usable cache entry. Separate processes receive distinct workspaces at the requested SHAs; editing or deleting files in one workspace does not change another. Coordinate test overlap with barriers/events rather than sleeps.
- [x] An advanced branch, new commit, and fork PR acquire missing Git data without rebuilding a healthy cache. A queued older SHA is used exactly or fails explicitly after a force push; the implementation never substitutes the newer head. Named refs refresh according to the documented policy.
- [x] A fresh cache restored from a valid full bundle can use an included commit without origin access, then fetch a newer one incrementally. Wrong-identity, corrupt, and prerequisite-dependent seeds fail safely. A fresh host without a seed is reported as a cold acquisition.
- [x] Cancellation, process force stop, worker death during population/fetch, and retry leave no published partial cache or reused dirty workspace. Surviving sibling workspaces remain usable, and abandoned resources are recoverable after owner death is established.
- [x] Lock contention, unreachable remotes, expired credentials, missing Git/revisions, disk pressure, and damaged cache entries produce bounded, actionable failures. Maintenance and eviction never remove active data, including when a queue lease expires while its old execution still lives.
- [x] Configured acquisition credentials are absent from Git command arguments, persisted config, queue data, seed artifacts, and retained logs. Path/ref injection, untrusted URL schemes, hooks/filters, and cross-scope reuse are covered. Submodule/LFS limits are visible to consumers.
- [x] Sync and async callbacks, per-attempt process execution, standalone use, SQLite, and Redis retain their existing execution semantics. Harness fixtures verify workspace handoff and lifetime without paid model calls; subprocess fixtures exercise real Git. Explicitly selected service/container checks establish deployment behavior beyond mocks.
- [x] Relevant pytest, Ruff, and mypy checks pass. Build and verify the installed wheel outside the source checkout using local Git fixtures; document the Git system dependency and supported platforms. Examples and local documentation links work, and verification results distinguish local fixtures from any separately selected live-provider checks.

## Implementation outcome

- Added `anyo.repositories.RepositoryCache` with sync/async context managers, exact-commit verification, refreshed refs, and a separate workspace per acquisition.
- Implemented persistent bare caches, process-safe locks, abandoned-owner recovery, inactive corruption repair, size/free-space checks, idle eviction, and explicit purge.
- Added separate HTTPS/SSH credentials, isolated Git configuration, supervised Git processes, and full bundle export/restore. Credentials are excluded from inherited model environments inside the checkout context.
- Kept the capability in `src/anyo/repositories/`: cache lifecycle, Git/credential handling, and filesystem coordination have separate responsibilities. Public imports stay in `anyo.repositories`; hooks, queues, and prompt signatures are unchanged.
- Fixed a force-stop race exposed by short-lived Git processes: reconcile pending start/stop notifications before signalling groups, finish owned cleanup after permission errors, and report unconfirmed cleanup as interrupted.
- Added [runnable project-pulling examples](../../../examples/repository-workspaces/README.md): a single branch or pinned commit, multiple projects held together, and exact PR preparation in a durable worker. They support trusted local fixtures, HTTPS credentials, configurable cache paths/namespaces, and cleanup after failures.
- Added the [short setup guide](../../wiki/repository-workspaces.md), [deployment guide](../../wiki/repository-deployment.md), and [final contract](../../knowledge/repository-workspaces.md). The Docker image includes the OpenSSH client.

## Verification

- **636 tests passed**, 37 live tests deselected, against a fresh installed wheel outside the checkout on macOS / Python 3.12. A base-dependency smoke check passed before installing test tools; imports resolved to `site-packages`.
- **53 repository tests passed** inside the rebuilt Linux ARM64 Docker image using Git 2.39.5. macOS checks used Git 2.54.0. Both sync/async callbacks and thread/process SQLite workers were exercised.
- **2 explicitly selected Redis tests passed** against an isolated real Redis 7.4 container, covering thread and process workers. Temporary services and volumes were removed.
- Two separate, network-disabled containers shared a persistent volume. After the first acquisition, the origin was made unavailable; the replacement container used the cached SHA successfully.
- Real local Git and authenticated local HTTPS fixtures covered concurrent acquisition, isolated edits, exact older PR commits, ref refresh, offline reuse, seed integrity/prerequisites, credentials, corruption, timeout, cancellation, worker death, and force stop. OpenSSH configuration was verified; authentication against a live SSH server was not exercised.
- Ruff checks and formatting passed for `src`, `tests`, and `examples`; strict mypy passed. These checks exclude the unrelated downloaded tools under `.anyo/`, which fail a workspace-wide Ruff scan in this checkout without Git metadata.
- Built the wheel from a fresh source archive, checked both with Twine, inspected module/resource contents, and passed `pip check`. Documentation links and new Python/shell example syntax passed.
- No live GitHub, Slack, or paid model calls were made. No package was published or deployed to production.

### Project example verification, 2026-10-11

The completed repository implementation was already present when the examples were expanded; it was retained and verified. The follow-up changed examples and their documentation without changing the library's public contract.

- Re-ran the default suite: **636 passed, 37 live tests deselected**. The focused repository run passed **53 tests**, with its 2 live tests deselected. Ruff checks/formatting and strict mypy passed.
- Built a fresh source archive and wheel, passed Twine and dependency checks, and inspected the repository modules and typing marker in the wheel. The examples ran outside the checkout using a clean base-dependency installation, with imports resolving to `site-packages`.
- Real local repositories verified branch refresh, multiple simultaneous project workspaces, exact older commits after the branch advanced, offline cache reuse in a new process, and cleanup when a later project failed. The original repositories stayed unchanged.
- The actual worker example accepted signed local webhook fixtures, recorded successful SQLite jobs, and released its workspaces. A replacement process succeeded after the origin was removed; a separate receiver and thread worker also reused that cache. The receiver had no clone-source configuration and the worker had no webhook signing secret.
- New example guide links and shell syntax were checked. This follow-up made no live-provider or model calls and did not repeat the earlier Docker/Redis verification.

## Sources

- Project conversation, 2026-10-11 (no share link supplied): requested project context from the docs and a task plan for cloning users' repositories with cache reuse when workers spawn; requested work on the plan alone.
- The linked project knowledge and read-only inspection of `anyo.hooks`, `anyo.agents`, GitHub event decoding, execution process supervision, and observability establish the current integration points.
- Official Git clone, worktree, fetch, and bundle documentation, plus the GitHub PR checkout guide linked above, checked on 2026-10-11. Their Git mechanisms inform the proposal; they do not establish Anyo-specific guarantees without implementation and verification.

- Project follow-up, 2026-10-11 (no share link supplied): authorized implementation and autonomous verification, requested work without delegation or clarification, and required project-pulling instructions in the examples folder.
