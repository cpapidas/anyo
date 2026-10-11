---
name: anyo-testing
description: Design, implement, and run tests for Anyo's Python library behavior. Use for regression coverage, public contracts, fake agents and providers, integration boundaries, failure handling, and asynchronous tests. Distribution build verification is covered by anyo-packaging.
---

# Anyo library testing

## For Humans

Anyo connects user code with agents and external systems whose responses can vary or fail. This skill keeps the library's behavior testable with predictable inputs and separates those checks from tests that need real services.

## Choose checks for the change

- Read the root [README](../../README.md), applicable `AGENTS.md` files, relevant contracts, and existing test configuration. Use the repository's selected runner and plugins; the references below do not select tools on their own.
- Follow [anyo-structure](../anyo-structure/SKILL.md) for test placement and imports. Reuse nearby fixtures and conventions before adding new helpers or test directories.
- Derive assertions from the consumer contract or a reproduced bug. For a bug fix, add a regression test that fails for the original behavior when practical. Avoid assertions that merely restate the implementation or check private call order without a behavioral reason.
- Select success, boundary, and failure cases that the change can affect. Documentation-only or mechanical changes do not require invented behavioral tests. Do not impose a coverage percentage in place of choosing meaningful cases.

## Keep ordinary tests deterministic

- Run core behavior tests without provider credentials, paid model calls, or external network access. Substitute small fakes at agent, provider, clock, or script-execution boundaries as needed; exercise the real Anyo behavior between those boundaries.
- Give fakes controlled outputs and failures and make them implement the same contract as the real dependency. Avoid replacing the object being tested or constructing a duplicate execution engine inside a test fake.
- Keep mutable run state local to a test. Use temporary paths and scoped environment changes; restore patches and release resources after failures as well as success. Promote fixtures to `conftest.py` only when actually shared.
- Assert meaningful outputs, state changes, raised exceptions, and required external effects. Match stable error context rather than entire incidental messages. Use call counts only when repetition itself is part of the contract.
- Prefer controlled clocks, seeded randomness, or injected values where behavior depends on time or randomness. Do not rely on wall-clock sleeps to order concurrent work.

## Exercise Anyo's boundaries

Use these cases when the corresponding behavior is implemented; they do not establish an execution or script protocol:

| Changed behavior | Useful test |
| --- | --- |
| Workflow composition | A later step receives the documented earlier result; a branch does not execute an unselected step. |
| User extension interface | A minimal user-defined callable or object works through the public API without a concrete provider dependency. |
| Provider adapter | Controlled transport responses map to the promised result or error; missing optional dependencies produce actionable errors when the integration is used. |
| Script execution | A small local fixture exercises the agreed output, exit-status, and cleanup contract. Mock process creation only when testing a caller rather than the runner itself. |
| Failure and retry behavior | Failures propagate or recover as specified, and observable effects repeat only as the documented policy allows. |
| Prompt evaluation | Fixed inputs and evaluator outputs verify scoring or selection logic without treating a live model answer as an exact expected string. |

For async behavior, use the selected async test support. Coordinate tasks with events or barriers, put bounded timeouts around potentially hanging operations, and verify cancellation and cleanup where relevant. Ensure tests leave no pending tasks or child processes; do not introduce an async runtime only for testing a synchronous feature.

## Separate live integration tests

- Keep local contract tests available in the default suite. Put live provider or service tests behind an explicit selection mechanism and require their configuration only when selected; credentials being present must not automatically trigger live calls.
- If using pytest, register custom markers and configure default deselection for live tests; a marker alone does not prevent execution. Use fixtures for setup and cleanup, and parameterization when several inputs test the same contract.
- Keep live runs bounded in calls, time, and generated resources. Test stable service contracts, not exact model prose. Report missing credentials or unavailable services as skipped or blocked checks rather than successful validation.
- Use the supported Python and dependency combinations declared by the project when configuring CI. Add matrix entries for supported behavior, not speculative environments.

## Run and report

- Run the smallest relevant tests while iterating, then the required checks for the change. Broaden testing when shared contracts or unresolved failures justify it; do not rerun unchanged passing suites repeatedly.
- Use [anyo-packaging](../anyo-packaging/SKILL.md) for wheel/source builds and tests against installed artifacts. An editable development install does not prove that a distribution contains everything it needs.
- Report the commands run, outcomes, and meaningful skips or limitations. Keep a failing regression visible rather than weakening its assertion or marking it expected to fail merely to make a run pass.

Consult [pytest's integration guidance](https://docs.pytest.org/en/stable/explanation/goodpractices.html) when configuring pytest and [its fixture guidance](https://docs.pytest.org/en/stable/how-to/fixtures.html) when designing fixture lifetimes. Keep tool-specific settings in the actual configuration.
