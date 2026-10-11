---
name: anyo-readme-wiki
description: Keep Anyo's root README short and write simple, example-led how-to guides in docs/wiki. Use when editing the README or wiki, moving usage documentation, or deciding where user-facing instructions belong.
---

# Anyo README and wiki

## For Humans

The README should quickly show what you get from Anyo and how to try it. Put the details in short wiki guides so readers can find just what they need.

## Choose the right place

Read the [root README](../../README.md), [documentation index](../../docs/README.md), and [wiki index](../../docs/wiki/README.md) before editing. Apply [anyo-docs](../anyo-docs/SKILL.md) for shared writing, knowledge, and task conventions.

| Content | Place | Example |
| --- | --- | --- |
| What Anyo does, its benefit, one tiny runnable example, and guide links | Root `README.md` | Receive a new pull request and print its number |
| How to do one thing, with setup and a small example | `docs/wiki/<topic>.md` | Connect Slack or run an AI prompt |
| Requirements, contracts, design decisions, and detailed limits | `docs/knowledge/<topic>.md` | Delivery guarantees and retry behavior |
| An agreed task's scope, progress, and verification | `docs/tasks/<task-name>/plan.md` | Implement a new integration |
| Complete runnable sample files | `examples/` with an entry in `examples/README.md` | A working review script and prompt file |
| Instructions specific to an existing code component | Its local `README.md` | Component ownership and maintenance |

Update an existing guide on the same topic before creating another. Link to the source of detailed information instead of copying it.

## Keep the root README thin

- Start with `# Anyo` and `## For Humans`. Explain the benefit before configuration or API details.
- Keep the human examples: **PR reviewer**, **bug analyzer**, and **team assistant**. Describe each in one short line as a workflow the reader can ask their AI model to build.
- State the benefit plainly: Anyo handles integrations and job execution, so the user's jobs stay clean and easy to read.
- State current integration support. GitHub and Slack are supported; more tools are planned. Update this statement when implementation changes, and keep plans distinct from shipped features.
- Include a short request a reader can give their AI model, such as: “Use Anyo to build a PR reviewer that reviews new pull requests and summarizes bugs and suggested fixes.”
- Explain the result in one simple flow: new PR → Anyo starts the job → AI reviews the changes → the user's code receives the review. Mention required setup and user-supplied review logic; do not imply a complete reviewer is built in.
- Keep one tiny runnable quick start with installation, necessary configuration, a short Python example, a run command, and the expected result. Label what it actually does: printing a PR number is not an AI review.
- End with a small set of action-based links and the wiki index. Move extra setup, configuration options, deployment, troubleshooting, and detailed behavior into the wiki.

## Write simple how-to guides

- Give each guide one clear goal, such as “Connect GitHub” or “Run separate workers.” Split a guide when it grows into several independent tasks.
- After the title, use `## For Humans` to say what the reader will accomplish in one or two short sentences.
- Show only the prerequisites needed for that goal, a small working example, and how to run it. Say what the reader should see when it works.
- Name files to create and values to replace. Include necessary imports and setup, or link directly to the prerequisite guide. Keep secrets as placeholders.
- Use short sentences, short paragraphs, and numbered steps when order matters. Explain unfamiliar terms where they first appear. Avoid long introductions and exhaustive option lists.
- Keep limitations that affect the example beside it. Link to knowledge documents for full contracts and design rationale.

For example, a Slack guide should show the needed app setup, a small callback, and the expected response. The full event inventory belongs behind a link.

## Move and verify

- When slimming the README, move useful instructions into the relevant guides before removing them. Preserve required setup, examples, and limitations.
- Add or update action-based entries in [the wiki index](../../docs/wiki/README.md). Keep [the documentation index](../../docs/README.md) and affected incoming links current, including heading anchors after moves.
- Check examples against the current public API and implementation. Keep workflow ideas, implemented features, and future plans distinct.
- Check changed Markdown links and code snippets. Run examples only when practical and already authorized; do not contact live services just to validate prose.
- Read the result as a first-time user: it should explain the benefit, show one clear next action, and link to the details without a wall of text.
