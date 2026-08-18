# Wiki 工作区

`wiki/` 是 `wiki-curation` skill 的运行时工作区：只放产物与运行时状态。skill 见 [github.com/yunhua-deng/wiki-curation](https://github.com/yunhua-deng/wiki-curation)。

## 目录说明

```
wiki/
├── artifacts/{id}/      # 单条知识的完整产物包（id 由 add 生成且不可变）
│   ├── record.json      # 结构化知识记录（link graph + TL;DR + tags + entities）
│   └── raw/             # 采集的原始素材（事实来源）
├── entities/{slug}/     # 实体综合层：可选 LLM 摘要（summary.md + meta.json）
├── data/wiki.db         # SQLite + FTS5：条目、链接、关系、队列（状态真相源，须提交 Git）
├── site/                # 构建好的静态站点（本地生成）
├── failures/            # 工作流失败案例库（模板：failures/TEMPLATE.md）
└── README.md            # 本文件
```

`wiki.db` 损坏或丢失时，可用 `cli.py sync --rebuild` 从 `artifacts/` 重建。

## 使用入口

能力、约束和 CLI 完整说明见 skill 安装位置下的 `SKILL.md`；CLI 为同目录 `scripts/cli.py`。站点预览：`python <skill安装位置>/scripts/cli.py site --serve`（默认 8123 端口）。
