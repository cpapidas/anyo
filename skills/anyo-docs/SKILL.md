---
name: anyo-docs
description: Create, update, and organize English Markdown documentation in Anyo, including how-to guides, knowledge, decisions, component READMEs, and agreed tasks. Use for documentation work rather than unrelated code changes.
---

# Anyo documentation

## For Humans

A project's story can become scattered across conversations, code, and task lists. This skill brings that story into concise documents that explain the context first, preserve the reasons behind decisions, and help readers find the next detail they need.

## Locate and scope

- Resolve paths from the project root containing this `skills/anyo-docs/SKILL.md` file. Read `docs/README.md`, applicable agent instructions, and relevant existing documents before writing.
- For the root README, wiki guides, and placement of usage instructions, also apply [anyo-readme-wiki](../anyo-readme-wiki/SKILL.md). Keep the root README short; put practical how-to guides in `docs/wiki/` and link them from its index.
- Determine whether the content concerns the project, a known component, or a proposal whose boundaries are undecided. Do not invent packages, applications, or services to organize documentation.
- Store shared knowledge in `docs/knowledge/` and component knowledge in `docs/knowledge/<component>/` when component boundaries are established. Store each agreed task in its own descriptive folder under `docs/tasks/`, following the task convention below. Keep operational component READMEs alongside their code and link to shared knowledge as appropriate.
- Create component folders only when needed. Update an existing document on the same subject rather than creating duplicate accounts. Link dependencies and decisions across components. Documentation organization does not prescribe Python package layout or application architecture.

## Write for readers

- Write all documentation in English. Use short English filenames in `kebab-case` and relative links between local documents.
- Every Markdown document, including indexes, instruction files, and this skill, starts with a title followed immediately by `## For Humans`. Required machine-readable frontmatter may precede the title; document metadata follows the introduction.
- In “For Humans,” introduce the real need and explain what the reader gets in a few short sentences. The root README may use brief workflow examples in a list; wiki introductions usually need only one or two sentences. Use plain language with no invented events or filler. Refresh it when the document's scope changes.
- Scale the rest of the structure to the content. Avoid empty headings, unnecessary templates, and repeating the same information in several places.

## Preserve knowledge and decisions

- Knowledge documents include status (`draft` or `accepted`), last-updated date, scope, and sources. Scope can be stated naturally in the text.
- Separate confirmed facts, proposals, and open questions. An assistant suggestion is not an accepted user decision; brainstorming is not automatically a backlog.
- Record what was decided and why, including alternatives and consequences actually discussed. Do not infer business rationale from code alone or invent supporting evidence.
- Cite available sources. When the source is a conversation without a link, identify it by date. Mark missing information explicitly.
- When a decision changes, preserve enough of the earlier rationale to explain the change without maintaining conflicting statements as current truth.

## Track agreed work

- Create tasks only for work requested or agreed to by the user.
- Give every task a dedicated folder at `docs/tasks/<descriptive-task-name>/`. Use an English `kebab-case` name describing the work, without a numeric prefix or a generic phase-only name. For example: `docs/tasks/custom-script-execution/`.
- The introductory document is always `plan.md` inside that folder. Keep any later supporting documents and task artifacts alongside it, adding them only when needed. Do not create standalone task plans directly under `docs/tasks/`.
- Link to the task's `plan.md` from indexes and related documents. Record affected components in the plan's scope; refer to tasks by descriptive names and relative paths, not numbered identifiers.
- Include the objective, scope, status (`todo`, `in-progress`, `blocked`, `done`), related knowledge, and verifiable completion criteria. Include owners, deadlines, dependencies, and blockers only when known.
- On completion, record the outcome and verification, update affected knowledge, and retain the task as history.

## Finish and verify

- Update `docs/README.md` when adding, moving, or removing documents indexed there. Use component indexes if the collection becomes large.
- Update `docs/wiki/README.md` when adding, moving, or removing how-to guides.
- Check changed documents for English prose, a useful opening “For Humans,” accurate status and scope, and working local links.
- Ensure proposals remain distinguishable from accepted decisions and tasks reflect actual authorization. Report the documents changed and any unresolved information.
