# Project instructions

## For Humans

Anyo is starting with a product direction and a shared place to record decisions. These instructions help contributors keep that context accurate as the Python framework takes shape, while leaving implementation choices open until they are decided.

## Scope

- Anyo is a Python AI pipeline framework. Read the root [README](README.md) and relevant local README and AGENTS.md files before making changes.
- Use the [product direction](docs/knowledge/product-overview.md) to distinguish agreed requirements from proposals and open questions.
- Anyo is a distributable Python library with one import package under `src/anyo/`. Follow the agreed package structure below; execution architecture and tooling remain open. Do not import Metis's Go layout or scaffolding conventions into Anyo.
- Keep changes scoped to the requested work and document dependencies between components when they affect it.

## Package structure

- When adding or moving Python modules, changing public imports or component dependencies, or reviewing package organization, read and apply [the anyo-structure skill](skills/anyo-structure/SKILL.md).
- Create capability modules only as implementation needs them. The skill's layout is a placement guide, not a requirement to create empty scaffolding.

## Documentation workflow

- When creating, updating, or organizing project documentation, read and apply [the anyo-docs skill](skills/anyo-docs/SKILL.md).
- When editing the root README or wiki, or deciding where usage instructions belong, also apply [anyo-readme-wiki](skills/anyo-readme-wiki/SKILL.md): keep the README thin and put simple how-to guides in `docs/wiki/`.
- Use [docs/README.md](docs/README.md) to locate existing guides, knowledge, and tasks before creating new documents.
- All skills are repository-local and loaded through these instructions; they require no global installation or execution hook.

## Library development

- When implementing or reviewing Python library code and public contracts, use [anyo-python](skills/anyo-python/SKILL.md).
- When designing, adding, or running behavior tests, use [anyo-testing](skills/anyo-testing/SKILL.md).
- When changing package metadata, dependencies, build contents, or preparing a release, use [anyo-packaging](skills/anyo-packaging/SKILL.md).
- Apply the skills relevant to the requested change. Tooling and runtime choices remain open until decided; references in a skill do not select a tool for the repository.
