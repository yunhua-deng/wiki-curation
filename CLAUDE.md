# CLAUDE.md

本仓库的维护守则**单源在 [`AGENTS.md`](AGENTS.md)** —— 契约测试、关键约束（FTS / 关系边 / 素材门禁 / append / 渲染必需来源）、目录结构约定、`publish` 与标识符约定、Git hook、bytecode 缓存陷阱，全部在那里。

改动本 skill 前请先读 `AGENTS.md`。提交前必须跑：

```bash
cd D:/wiki-curation
python -m pytest scripts/ -q
python eval/run_eval.py --deterministic
```

> 本文件过去与 `AGENTS.md` 逐字重复（两份互相漂移）。2026-09-15 起收敛为指针，只保留上面这条提交前必跑项。
