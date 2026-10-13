# Open logged tasks and inspect their logs

## For Humans

An operator should be able to find a task in the dashboard, click it, and read its logs. This task improves that path, including retained logs whose queue job has already expired.

Status: done  
Last updated: 2026-10-13

## Objective and scope

Provide discoverable task-to-log navigation in the existing protected operations UI. Treat a pipeline run as the logged task, preserving its run identity and attempt context. Inspect the current UI and storage before selecting the implementation; storage changes are authorized where needed to keep retained logs accessible.

Affected components are the packaged UI, its authenticated HTTP API, operations contracts, SQLite/Redis adapters as needed, and behavior tests. Keep current worker execution, authentication, redaction, bounded retention, and mounted-host support intact.

## Work

- Inspect existing run detail navigation, log queries, and the independent job/log retention rules.
- Implement a clear list of logged tasks and a selected-task view with correlated history, live updates, and useful empty/error states.
- Preserve navigation when a queue job expires but its logs remain. Keep general logs accessible and distinguish them from task logs.
- Verify task isolation, attempts, retention, navigation, and authenticated/mounted routes. Update the dashboard guide and operations contract with the delivered behavior.

## Completion criteria

- [x] A user can click a logged task and read only that task's logs without manually copying an ID.
- [x] Task identity, attempt context, and navigation remain clear while live logs arrive and when returning to the list.
- [x] Retained task logs remain discoverable after queue job expiry; unavailable metadata and expired logs are explained honestly.
- [x] General logs, existing controls, authentication, escaping/redaction, and mounted deployments continue to work.
- [x] Relevant regression tests, static checks, and a browser check pass; actual results and limits are recorded here.
- [x] The documentation index, dashboard guide, and operations contract describe the implementation.

## Outcome

Logs now opens a searchable **Logged tasks** list. Task names and **View logs** links open a dedicated view with source/severity filters, live pause/resume, explicit history pagination, attempt/prompt/worker context, and collapsible execution details. **All activity** retains the combined stream and links each attributed entry to its task. URL fragments preserve selection through refresh, sign-in, and browser history, including mounted hosts.

SQLite and Redis derive the task list from retained logs independently of queue job history. New logs snapshot a known job's hook name; SQLite adds a run/cursor index. Existing log records remain readable, and the optional compact task-index contract preserves third-party operations backends through their existing paginated log interface. No unbounded task archive or new runtime dependency was added. Older records without a saved label use their run ID when queue metadata is also gone. Same-namespace processes must upgrade together because older SQLite readers cannot decode the added log JSON field.

## Verification

Checks completed on 2026-10-13 using Python 3.12, Node 24, and local Redis 7.4:

| Check | Result |
| --- | --- |
| Full suite against installed wheel | 881 passed, 43 live tests deselected. |
| Copied UI, operations storage, and observability tests outside the checkout | 42 passed, 18 live cases deselected. |
| Real Redis operations tests against installed wheel | 17 passed; one separately gated server-restart test skipped. |
| Static checks | Ruff lint, formatting for source/tests/changed docs, mypy (57 source files), JavaScript syntax, and `git diff --check` passed. |
| Browser | Mounted `/anyo/ui/`: task search, expired-job logs, 246-entry pagination, attempt labels, source/severity filtering, escaped script text, activity-stream task links, Back/Forward, refresh, and bookmarked-task sign-in verified. Pause held 9 displayed entries while retained output grew to 36; resume caught up to 36. Narrow-window layout checked visually. |
| Distribution | Source archive and wheel-from-source-archive built; metadata checks passed. All 64 runtime files matched the source byte-for-byte. A fresh base-only installation loaded UI assets and exercised stored task discovery; dependency consistency passed. |
| Documentation | Changed documents' required openings and local file links checked. |

Browser verification found a same-fragment login navigation bug; the login now reloads the authenticated document while retaining its fragment. A regression test executes the packaged login JavaScript, covering both successful and failed sign-in. Storage regressions cover task isolation, multiple attempts, capture ordering despite clock skew, queue expiry, count/time retention, reopening storage, namespace isolation, and the optional-index fallback.

At initial verification, the repository-wide `ruff format --check .` reported two pre-existing formatting issues in `docs/knowledge/worker-queues.md` and `docs/wiki/repository-deployment.md`. Those unrelated files were left unchanged by this task; formatting checks passed for every changed document and all Python source/tests. No production deployment, publication, paid model call, or live provider request was needed.

PR preparation on 2026-10-13 rebased the change onto current `main`, preserving its release work and resolving only the documentation-index overlap. The updated default suite passed **951 tests**, with 43 live cases deselected. Repository-wide Ruff lint and formatting (187 files), mypy, JavaScript syntax, and whitespace checks all passed; the earlier documentation formatting issues had already been fixed on `main`.

## Context and sources

- Project conversation, 2026-10-13 (no share link supplied): requested project context from docs, a task created before implementation, clickable logged tasks with their logs, permission to change storage, and autonomous work without delegation or clarification.
- [Product direction](../../knowledge/product-overview.md), [operations contract](../../knowledge/operations-ui.md), and [dashboard guide](../../wiki/operations-ui.md).
- [Original operations UI task](../protected-operations-ui/plan.md): existing runs, controls, authentication, and log collection.
- [HTTP hosting task](../embeddable-http-application/plan.md): mounted UI behavior that must remain supported.
