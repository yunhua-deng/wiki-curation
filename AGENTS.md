# wiki-curation 维护守则

本 repo 是 wiki-curation skill 的单源。各 agent 通过用户目录下的标准 skill 加载点使用本 skill 的 clone（OpenClaw / Kimi Code: `~/.agents/skills/wiki-curation`，Kimi Code 另有 `~/.kimi-code/skills/wiki-curation`，Claude Code: `~/.claude/skills/wiki-curation`）；工作区只有一个 `D:/wiki-workspace`（所有 agent 共用，也是 OpenClaw 的工作区）。每次修改本 skill 后，必须先通过契约测试，再汇报完成；推送后需在各 clone `git pull` 同步——**注意 `~/.kimi-code/skills/wiki-curation` 是指向 `D:/wiki-curation` 的符号链接**，它不需要 pull（改动即时生效），只有前两处是独立 clone。（`openclaw skills update` 只作用于从 ClawHub 安装的 skill，不会同步这些 clone。）

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
- `entries_fts` 存的是 **CJK 逐字切开**后的 `search_text`，不是原文：索引侧（`store._insert_entry`）与查询侧（`store.search` / `recall._fts_query_or`）必须共用 `scripts/wiki_index/fts_text.py`。两侧不一致时中文子串召回会**静默退化**——v9 之前默认 unicode61 把连续汉字当单个 token，实测「智能」召回率仅 1%（1/186）。
- 查询表达式只用 `fts_text.to_match_expr()` 生成：长度 ≥ `BIGRAM_MIN_CJK`(5) 的无空格 CJK 串会展开成「整串短语 OR 相邻二字组」，否则「机器人抓取策略」这类中文查询要求整串连续出现而召回为空。另注意多词之间必须写**显式 ` AND `**，FTS5 不接受括号组与相邻短语之间的隐式 AND（`("a" OR "b") "c"` 会语法报错）。
- `handler_webpage` 的材料有效性按**可见正文密度**判定：剥掉 script/style/标签后的可见字符数须 ≥ `settings.min_visible_chars`（默认 800）。只看 HTTP 200 + 文件字节数会把 SPA 外壳（Vite/React/Next CSR，正文由 JS 渲染）判成 `success`；正文不足判 `failed`，并在 `_fetch_results.json` 写入 `visible_chars` / `spa_shell` 供 orchestration 决策。
- `run` 的**素材门禁**：URL 类声明来源必须在 `raw/**/_drill_log.json` 的 level-1 条目里拿到 `status: success`，否则 `run` 返回 `MATERIALS_MISSING`（`detail.missing`）、不设 `materials_ready`、不生成提取任务；`--accept-manual` 可显式降级为告警（`FETCH: manual (accepted)`）。判定依据是**「声明来源 vs 抓取证据」**，不是「raw/ 目录是否非空」——不要退回「目录非空即复用」的旧逻辑。
- `add --append-to` 只允许对 `status=done` 的条目；append 的新素材落 `raw/append_<N>/`（自带 `_drill_log.json` / `_fetch_results.json`），**不得覆盖既有材料**；append 意图由 `ENQUEUE` 事件承载，普通 `run --id <slug>` 也要能识别。
- **有副作用的步骤不重试**：`cli.py` 对 `run` / `collect` / `ingest` 一律 `retries=0`（`_run_script(..., retries=0)`），`orchestrate.run_script()` 调子脚本同样 `retries=0`。`run_cmd` 默认 `retries=1` 且对**任意非零退出码**都重试，而这些步骤的非零退出码是正常失败信号（`MATERIALS_MISSING` / `COLLECT_FAILED`）——曾导致整条流水线跑两遍（重复抓取、多出一层 `append_N`、2×超时叠加）。要重试就重试抓取本身（`settings.render_retries` / `--force-collect`）。
- **渲染必需来源**（登录态 / JS 渲染 / 反爬 / 容器型应用）由 `references/sources.yaml` 的 `render_required`（subtypes / domains / path_patterns / url_markers）统一判定：先按 `settings.render_retries` 重试轻量路径（curl / opencli weixin），再回退浏览器渲染，产物 `raw/<file_stem>_rendered.html|.md`；每次尝试都写 `_fetch_results.json`（带 `attempt`）。**非渲染必需来源必须保持原轻量路径不变**（`render_required` 缺失/损坏一律按「非渲染必需」处理）。判定仍只看可见正文密度，不看「文件是否存在」。
- **`sync --rebuild` 必须真的重建索引**：遍历 `artifacts/*/record.json`（事实源），复用 publish 的同套原语回填 `entries` / `entries_fts` / `links` / `relations`；**只增不删**、保留既有队列元数据（running 不会被改成 done）、没有 `record.json` 的目录忽略。它是 wiki.db 丢失后的唯一恢复路径——历史上这里是空实现（返回计数却什么都不做），**不得退回**。它不恢复 `events` 审计流与 `record.preview`。
- `doctor` 的迁移版本排序按**数字**（v11 > v9），不是字典序；新增 v10+ 迁移时留意任何 `sorted(applied)`。
- **浏览器探测只作诊断，绝不能当闸门**：`_browser_fetch` 里的 `openclaw browser tabs` 探测与实际抓取用的 `opencli browser` **不是同一个后端**，探测结论只写进 `_fetch_results.json` 的 `probe` 字段；探测失败时仍必须尝试 opencli（否则 opencli 明明可用也会被误判成「浏览器不可用」，`2026-09-15_005` 就是这个问题）。状态由真实后端给：opencli 起不来（exit `-2`）→ `needs_browser`；页面打开但正文过短 → `failed`。

## publish 与标识符约定

- `publish` 是 wiki 写入流程的**唯一收口点**（`publish/commands.py` → `records/publish_record.py`）：校验 record.json、fetched 回填、links/relations/entities 入库、站点刷新。
- v3.3：`publish --id X` = 记录发布；`publish --id X --depth brief|deep` = 历史文章标记 done（不做 verify_output）——**legacy 兼容路径**，v3.1 起新流程不再产生 `<id>_<depth>.md`，现存文件已归档到工作区 `wiki/archive/deep/`。
- `orchestrate.py`（`run` 命令）不执行 rename，只输出 spawn JSON（record 唯一模式；`--depth`/`--mode article` 返回 `DEPRECATED_MODE`，保留仅为给出明确错误）。
- `publish` 内部通过 `wiki/.publish.lock` 文件锁串行化；返回 `BUSY` 应等待重试（错误信息含持有者 pid / host / 锁龄）。
- 锁目录内写 `owner.json`（pid / host / started_at）：进程被强杀留下的残留锁，在锁龄超过 `stale_after`(600s) 且持有者已消失时由下一个 publisher **自动接管**（stderr 打 WARN），不再永久阻塞 publish；持有者仍存活时绝不抢占。
- **entry ID 不可变**：hash-based slug 在 `add` 时生成，后续命令始终使用同一个 ID（历史异常 id 除外，见 `wiki/docs/issues/` 修复记录）。

## 目录结构约定

静态配置只保留一个源头，放在 skill 内部：

- `references/` —— agent 需要读取的知识/规则：
  - `sources.yaml`（来源类型、分类规则）
  - `record_schema.json`（record.json 约束常量，records/schema.py 消费）
  - `entity_aliases.yaml`（实体 canonical/别名映射 + suppress 抑制名单；`scripts/entity_filter.py` 是唯一读取入口，recall 实体层与站点搜索扩展也从这里取）
  - `entity_groups.yaml`（实体五类分组 + academia_keywords；供 `entity_filter.py` 分组查询与 canonical 豁免）
- `assets/` —— 前端静态资源：
  - `assets/site/`（site.js / site.css）
- 工作区骨架（`scripts/bootstrap.py` 的 `SKELETON_DIRS`）：`artifacts/` / `data/` / `docs/` / `docs/issues/` —— 实体综合层目录 `entities/` 已废除；问题单登记表（bug + feature 共用一份）在 `docs/issues/`。

> 配置的唯一来源就是 skill 内的 `references/`；不再有 `wiki/configs/` 这类工作区运行时覆盖目录。

## 关于 `eval/` 和 `tests/`

这两个目录不是 skill 内容，而是本地工程测试设施：

- `eval/`：本地评测脚本（deterministic 检查 + 可选 LLM-rubric，见 `eval/eval.yaml`）。
- `tests/`：pytest fixtures。

## Git Hook 说明

契约测试通过仓库根目录的 `.githooks/pre-commit` 触发。

- **`.githooks/` 是被 Git 跟踪的**，所以会随代码一起推送到 GitHub；其他 clone 下来也能拿到 hook 源码。
- **`.git/hooks/` 默认不被 Git 跟踪**，只存在于本地，所以不能直接把 hook 放在那里。
- 为了让 Git 使用 `.githooks/` 而不是默认的 `.git/hooks/`，需要在本仓库执行一次：

```bash
git config core.hooksPath .githooks
```

**Hook 的触发范围**：只有暂存区里出现 `scripts/` / `references/` / `assets/` / `SKILL.md` / `pyproject.toml` 的改动时，hook 才会跑契约测试（其余提交零延迟放行）。所以**改 `eval/` 或 `tests/` 时必须手动跑**上面两条命令。

不要采用「把 hook 复制进 `.git/hooks/`」的替代做法：`core.hooksPath .githooks` 生效后两者会各跑一遍，而复制出来的那份还会随 `.githooks/` 的更新而失效。

## Bytecode 缓存陷阱

`scripts/` 下的模块经常以子进程方式被 `cli.py` 调用。如果在修改源码后运行时行为未变，应首先怀疑 stale `__pycache__`：

- 表现：源码已修复，手动复现成功，但 `cli.py` 子进程路径仍失败。
- 根因：Python 可能仍在加载旧的 `.pyc` 文件。
- 处理：
  1. `cli.py` 已禁用 bytecode 写入并在启动时清理 `__pycache__`，通常无需手动干预。
  2. 若仍遇到可疑行为，可手动清理：`find scripts -type d -name __pycache__ -exec rm -rf {} +`
  3. 契约测试脚本（`run_contract_tests.ps1` / `.sh`）每次运行前也会清理缓存，确保测试的是当前源码。

相关回归记录：`wiki/docs/issues/2026-07-09_005_linkedin-handler-invalid-command.md`
