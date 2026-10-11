---
name: anyo-python
description: Implement or review Anyo's Python library code and public APIs. Use for function and class contracts, type annotations, extension interfaces, exceptions, resource ownership, and compatibility changes. Package placement is covered by anyo-structure.
---

# Anyo Python library development

## For Humans

Developers will combine Anyo with their own functions, agents, scripts, and application infrastructure. This skill guides library code that is clear to call, possible to extend, and predictable when something fails or an API changes.

## Establish the contract

- Read the root [README](../../README.md), applicable `AGENTS.md` files, and relevant [product direction](../../docs/knowledge/product-overview.md). Inspect existing public APIs, tests, and tooling configuration before editing.
- Follow [anyo-structure](../anyo-structure/SKILL.md) when placement, imports, or dependencies change. Keep formatting, lint rules, and type-checker settings in the configured tools rather than duplicating them here.
- Establish the intended call pattern, accepted inputs, return value, failure behavior, and resource ownership for the requested feature. Use a small consumer example to resolve ambiguous API choices when useful.
- Python baseline, concrete authoring APIs, and execution architecture remain separate decisions. Do not silently establish a new minimum Python version, data-model framework, or concurrency model while implementing an unrelated feature.

## Design typed, extensible APIs

- Annotate public inputs and outputs, including callbacks and extension interfaces. Use syntax and standard-library features supported by the declared Python baseline.
- Accept the least restrictive interface the implementation actually needs. For example, accept a mapping when only reading keys; require a mutable mapping only when mutation is part of the contract. Be explicit when an iterable is consumed once or must support repeated traversal.
- Prefer a callable for a single operation. Use a small protocol when an extension needs several related operations without requiring users to inherit an Anyo class. Introduce a base class only when shared behavior or an enforced lifecycle justifies it.
- Keep external SDK types inside their adapters unless exposing them is an intentional public contract. A user-supplied agent or evaluator should be implementable without importing an unrelated provider SDK.
- Make output types and ownership explicit. Use `object` for an arbitrary value that must be inspected before use; keep unavoidable `Any` at external boundaries and narrow it before internal use. Do not silence type errors with broad casts or ignores instead of fixing a contract.
- Choose records, mappings, or classes to match the behavior needed. Do not introduce a validation dependency or class hierarchy merely to hold a few fields. Type annotations do not perform runtime validation; validate untrusted boundary data where required.
- Avoid shared mutable defaults and hidden mutation of caller-owned inputs. Document intentional mutation, copying, and whether returned data is a snapshot or a live view.

## Fail clearly and respect the host application

- Raise exceptions for failures rather than printing an error and returning an ambiguous success value. Prefer built-in exceptions for ordinary invalid arguments; add domain exceptions when consumers need to distinguish and handle them.
- Translate provider failures at the adapter boundary only when Anyo promises a provider-independent error. Preserve the cause and useful context without including credentials or entire prompts in default error messages.
- Catch only failures the code can handle. Do not convert cancellation or process-control exceptions into successful results, fallback values, or retries. Retry policy belongs to the execution contract.
- Let the host application configure logging. Library code may use module loggers; it must not configure the root logger, call `sys.exit`, or change the process working directory as an ordinary API side effect.
- Distinguish resources Anyo creates from resources supplied by the caller. Close owned clients, files, and workers on success and failure; do not close a borrowed resource unless ownership was explicitly transferred. Use context managers when they clarify that lifecycle.
- Follow the agreed sync or async contract. For async implementations, avoid blocking the event loop and preserve cancellation through cleanup. Do not add event-loop runners inside awaitable APIs or fabricate paired sync/async APIs without a requirement.

## Evolve the consumer contract

- Review changes to keyword parameter names, defaults, return shapes, exceptions, type annotations, and sync/async behavior as compatibility changes, even when the import path stays the same.
- Before changing an existing contract, inspect its tests and examples. Follow the established compatibility policy; if none exists, make the impact explicit in the requested design work rather than inventing a release schedule.
- When a migration needs a deprecation, provide a replacement and a focused example. Use the agreed warning policy and test that the warning points to consumer code where appropriate.
- Document public behavior beside the API: meaningful parameters, results, exceptions, mutation, and lifecycle. Use [anyo-docs](../anyo-docs/SKILL.md) for project documentation changes without duplicating the API contract across documents.

## Verify the change

- Apply [anyo-testing](../anyo-testing/SKILL.md) to changed behavior and run the relevant configured tests, lint, and type checks. Report unavailable tooling as unverified; do not claim checks passed because annotations are present.
- For extension interfaces, check a small consumer-defined implementation against the contract. Verify both its runtime behavior and typing when type checking is configured.
- For public API changes, check affected examples and imports and report compatibility implications. Use [anyo-packaging](../anyo-packaging/SKILL.md) when the change affects dependencies or distribution contents.

Consult [Python typing best practices](https://typing.python.org/en/latest/reference/best_practices.html) for annotation decisions. Match guidance to Anyo's supported Python versions and actual API requirements.
