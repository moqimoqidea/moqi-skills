# moqi-skills

个人维护的 [Agent Skills](https://agentskills.io/) 集合，供 Claude / Codex / 各类 Agent CLI 加载使用。
文件结构参考 [anthropics/skills](https://github.com/anthropics/skills)。

## 目录结构

```
.
├── skills/       # 所有 skill 存放于此，每个子文件夹是一个独立 skill
├── template/     # 新建 skill 时使用的 SKILL.md 模板
└── README.md
```

详见 [skills/README.md](skills/README.md) 了解每个 skill 的目录约定与索引列表。

## 使用方式

- **Claude Code / Claude.ai**：将某个 `skills/<name>/` 文件夹作为自定义 skill 上传，或参考
  [Using skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude)。
- **其他 Agent**：将 `SKILL.md` 的内容作为系统提示/工具说明的一部分加载即可。

## 新增一个 skill

1. 复制 [`template/SKILL.md`](template/SKILL.md) 到 `skills/<skill-name>/SKILL.md`。
2. 填写 YAML frontmatter 中的 `name`、`description`，并编写具体指令。
3. 按需添加 `scripts/`、`references/`、`evidence/`、`agents/` 子目录。
4. 在 [skills/README.md](skills/README.md) 的索引表中补充一行。

## License

个人仓库，未特别说明的内容默认保留所有权利；引用的第三方资料以其原始来源许可为准。
