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

- 不要破坏 `cli.py --json manifest` / `run` / `doctor` / `stats` / `classify` / `recall` / `analyze` 的 JSON 契约。
- 仅允许 `cli.py` 包含 `sys.path.insert` 条件引导。
- 不要提交 `__pycache__`、`.pytest_cache`、`*.egg-info`。
- 新增依赖必须写入 `pyproject.toml`。

## publish 与标识符约定

- `publish` 是 wiki 写入流程的**唯一收口点**（`publish/commands.py` → `records/publish_record.py`）：校验 record.json、fetched 回填、links/relations/entities 入库、站点刷新。
- v3.3：`publish --id X` = 记录发布；`publish --id X --depth brief|deep` = 历史文章标记 done（不做 verify_output）。
- `orchestrate.py`（`run` 命令）不执行 rename，只输出 spawn JSON（record 唯一模式；`--depth`/`--mode article` 返回 DEPRECATED_MODE）。
- `publish` 内部通过 `wiki/.publish.lock` 文件锁串行化；返回 `BUSY` 应等待重试。
- **entry ID 不可变**：hash-based slug 在 `add` 时生成，后续命令始终使用同一个 ID（历史异常 id 除外，见 `wiki/failures/` 修复记录）。

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

相关回归记录：`wiki/failures/2026-07-09_005_linkedin-handler-invalid-command.md`
