# Skills

Each subfolder here is one self-contained skill, structured the same way as
[anthropics/skills](https://github.com/anthropics/skills):

```
skills/<skill-name>/
├── SKILL.md          # required — YAML frontmatter (name, description) + instructions
├── scripts/           # optional — executable helper scripts the skill invokes
├── references/        # optional — longer docs loaded on demand (schemas, notes)
├── evidence/           # optional — cached/checked-in sample data or snapshots
└── agents/             # optional — agent-platform-specific config (e.g. openai.yaml)
```

Only `SKILL.md` is required. Add the other folders only when the skill actually
needs them.

## Index

| Skill | Description |
| --- | --- |
| [model-price](model-price/SKILL.md) | Query and compare major models and official prices across Chinese cloud platforms, OpenAI, Anthropic, and Google Gemini. |

## Adding a new skill

1. Copy [`template/SKILL.md`](../template/SKILL.md) into a new folder under `skills/<skill-name>/`.
2. Fill in the `name` and `description` frontmatter and write the instructions.
3. Add `scripts/`, `references/`, `evidence/`, or `agents/` only as needed.
4. Add a row to the index table above.
