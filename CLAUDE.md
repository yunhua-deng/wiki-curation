# wiki-curation 维护守则

本 repo 是 wiki-curation skill 的单源（下游工作区如 D:/openclaw-workspace 通过根 AGENTS.md 指向 `D:/wiki-curation/SKILL.md` 使用本 skill）。每次修改本 skill 后，必须先通过契约测试，再汇报完成。

## 本地契约测试（每次提交前必跑）

```powershell
cd D:\wiki-curation
python -m pytest scripts/ -q
python eval/run_eval.py --deterministic
```

或者运行已封装的脚本：

```powershell
.\scripts\run_contract_tests.ps1
```

## 回归测试（非必须，重大改动时跑）

```powershell
$env:WIKI_EVAL_MODEL = "kimi/kimi-for-coding"
python eval/run_eval.py --llm
```

## 关键约束

- 不要破坏 `cli.py --json manifest` / `run` / `doctor` / `stats` / `classify` / `recall` / `analyze` / `publish` 的 JSON 契约；`entities` 只保留只读契约（`--list` / `--name X`，无 watch / 摘要字段）。
- 仅允许 `cli.py` 包含 `sys.path.insert` 条件引导。
- 不要提交 `__pycache__`、`.pytest_cache`、`*.egg-info`。
- 新增依赖必须写入 `pyproject.toml`。
- `relations` 表只存**结构边**：`same_url` / `shared_link` / `tag_overlap`（实体驱动的 `shared_entity` 边已废除，存量库由 v8 迁移清理）。
- `entries_fts` 存的是 **CJK 逐字切开**后的 `search_text`，不是原文：索引侧（`store._insert_entry`）与查询侧（`store.search` / `recall._fts_query_or`）必须共用 `scripts/wiki_index/fts_text.py`。两侧不一致时中文子串召回会**静默退化**（v9 之前「智能」召回率仅 1%）。
- 查询表达式只用 `fts_text.to_match_expr()` 生成：长度 ≥ `BIGRAM_MIN_CJK`(5) 的无空格 CJK 串会展开成「整串短语 OR 相邻二字组」。多词之间必须写**显式 ` AND `**，FTS5 不接受括号组与相邻短语之间的隐式 AND。
- `handler_webpage` 的材料有效性按**可见正文密度**判定：剥掉 script/style/标签后的可见字符数须 ≥ `settings.min_visible_chars`（默认 800）。只看 HTTP 200 + 文件字节数会把 SPA 外壳（正文由 JS 渲染）判成 `success`；正文不足判 `failed` 并在 `_fetch_results.json` 标记 `visible_chars` / `spa_shell`。
- `run` 的**素材门禁**：URL 类声明来源必须在 `raw/**/_drill_log.json` 的 level-1 条目里拿到 `status: success`，否则 `run` 返回 `MATERIALS_MISSING`（`detail.missing`）、不设 `materials_ready`、不生成提取任务；`--accept-manual` 可显式降级为告警（`FETCH: manual (accepted)`）。判定依据是**「声明来源 vs 抓取证据」**，不是「raw/ 目录是否非空」。
- `add --append-to` 只允许对 `status=done` 的条目；append 的新素材落 `raw/append_<N>/`，**不得覆盖既有材料**；append 意图由 `ENQUEUE` 事件承载，普通 `run --id <slug>` 也要能识别。
- **渲染必需来源**由 `references/sources.yaml` 的 `render_required` 统一判定：先按 `settings.render_retries` 重试轻量路径，再回退浏览器渲染（产物 `raw/<file_stem>_rendered.html|.md`，每次尝试写 `_fetch_results.json` 并带 `attempt`）；非渲染必需来源必须保持原轻量路径不变。
- **有副作用的步骤不重试**：`cli.py` 对 `run` / `collect` / `ingest` 一律 `retries=0`，`orchestrate.run_script()` 亦然。`run_cmd` 默认 `retries=1` 且对任意非零退出码都重试，会把这些步骤的正常失败信号（`MATERIALS_MISSING` 等）变成「流水线跑两遍」。

## publish 与标识符约定

- `publish` 是 wiki 写入流程的**唯一收口点**（`publish/commands.py` → `records/publish_record.py`）：校验 record.json、fetched 回填、links/relations/entities 入库、站点刷新。
- v3.3：`publish --id X` = 记录发布；`publish --id X --depth brief|deep` = 历史文章标记 done（不做 verify_output）。
- `orchestrate.py`（`run` 命令）不执行 rename，只输出 spawn JSON（record 唯一模式；`--depth`/`--mode article` 返回 DEPRECATED_MODE）。
- `publish` 内部通过 `wiki/.publish.lock` 文件锁串行化；返回 `BUSY` 应等待重试（错误信息含持有者 pid / host / 锁龄）。
- 锁目录内写 `owner.json`（pid / host / started_at）：残留锁在锁龄超过 `stale_after`(600s) 且持有者已消失时自动接管，持有者存活时绝不抢占。
- **entry ID 不可变**：hash-based slug 在 `add` 时生成，后续命令始终使用同一个 ID（历史异常 id 除外，见 `wiki/docs/issues/` 修复记录）。

## 目录结构约定

静态配置只保留一个源头，放在 skill 内部：

- `references/` —— agent 需要读取的知识/规则：
  - `sources.yaml`（来源类型、分类规则）
  - `record_schema.json`（record.json 约束常量，records/schema.py 消费）
  - `entity_aliases.yaml`（实体 canonical/别名映射 + suppress 抑制名单）
  - `entity_groups.yaml`（实体五类分组 + academia_keywords）
- `assets/` —— 前端静态资源：
  - `assets/site/`（site.js / site.css / marked.min.js）

> 不再维护 `wiki/configs/` 运行时覆盖目录，避免双源头。

## 关于 `eval/` 和 `tests/`

这两个目录不属于 skills-best-practices 定义的 skill 内容，而是本地工程测试设施：

- `eval/`：skillgrade 风格的本地评测脚本（deterministic + 可选 LLM-rubric）。
- `tests/`：pytest fixtures。

在未引入官方 skillgrade CLI 前保留它们。

## Git Hook 说明

契约测试通过仓库根目录的 `.githooks/pre-commit` 触发。

- **`.githooks/` 是被 Git 跟踪的**，所以会随代码一起推送到 GitHub；其他 clone 下来也能拿到 hook 源码。
- **`.git/hooks/` 默认不被 Git 跟踪**，只存在于本地，所以不能直接把 hook 放在那里。
- 为了让 Git 使用 `.githooks/` 而不是默认的 `.git/hooks/`，需要在本仓库执行一次：

```bash
git config core.hooksPath .githooks
```

如果你用的 coding agent 没有读到这个配置，导致它去 `.git/hooks/` 找不到 hook，告诉它 hook 在 `.githooks/pre-commit`，或者先执行上面的配置命令。

### 一次性安装脚本（可选）

```powershell
# Windows
Copy-Item ..\..\.githooks\pre-commit ..\..\.git\hooks\pre-commit
```

```bash
# Linux/macOS
cp ../../.githooks/pre-commit ../../.git/hooks/pre-commit
chmod +x ../../.git/hooks/pre-commit
```

## Bytecode 缓存陷阱

`scripts/` 下的模块经常以子进程方式被 `cli.py` 调用。如果在修改源码后运行时行为未变，应首先怀疑 stale `__pycache__`：

- 表现：源码已修复，手动复现成功，但 `cli.py` 子进程路径仍失败。
- 根因：Python 可能仍在加载旧的 `.pyc` 文件。
- 处理：
  1. `cli.py` 已禁用 bytecode 写入并在启动时清理 `__pycache__`，通常无需手动干预。
  2. 若仍遇到可疑行为，可手动清理：`find scripts -type d -name __pycache__ -exec rm -rf {} +`
  3. 契约测试脚本（`run_contract_tests.ps1` / `.sh`）每次运行前也会清理缓存，确保测试的是当前源码。

相关回归记录：`wiki/docs/issues/2026-07-09_005_linkedin-handler-invalid-command.md`
