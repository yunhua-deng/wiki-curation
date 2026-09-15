#!/usr/bin/env python3
"""
skills/wiki-curation/scripts/test_orchestrate.py — orchestrate.py / CLI 编排与工作流门禁测试。

设计原则：
- 使用 pytest fixtures 替代 unittest shim。
- 核心路径（输入解析、分类聚合、收集路由、追加输出路径、工作流门禁）用 mock 验证。
"""
import json
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

from scripts import conftest
from scripts import intake
from scripts import paths
from scripts import wiki_index
from scripts.conftest import SCRIPT_DIR, seed_entry
from scripts.exec import orchestrate
from scripts.intake import commands as intake_cmds
from scripts.publish import commands as publish_cmds


FIXTURES_DIR = SCRIPT_DIR.parent / "tests" / "fixtures"


def _make_args(**kwargs):
    defaults = {
        "id": None,
        "mode": None,
        "depth": None,
        "append_to": None,
        "max_depth": 3,
        "force_collect": False,
        "json": True,
        "quiet": True,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def _patch_db_paths(tmp: Path, monkeypatch):
    """把 orchestrate / paths 的数据库路径指向临时目录。"""
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestrate, "WORKSPACE", tmp)
    monkeypatch.setattr(orchestrate, "DB_PATH", paths.db_path(tmp))
    monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp)


def _drill_entry(index: int, source_input: str, status: str = "success") -> dict:
    return {
        "level": 1, "label": f"L1-s{index}", "subtype": "webpage",
        "input": source_input, "status": status,
        "files": [f"s{index}/webpage.html"] if status == "success" else [],
        "drill_targets": [], "error": "", "note": "", "parent": "",
    }


def _write_drill_log(raw_dir: Path, entries, filename: str = "_drill_log.json") -> Path:
    """写一份最小可用的 _drill_log.json；level-1 条目是「声明来源是否已抓取」的真相源。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": raw_dir.parent.name, "started_at": "", "max_depth": 1,
        "primary_type": "multi_source", "primary_input": "",
        "levels": [{"level": 1,
                    "entries": [_drill_entry(i, inp, st) for i, (inp, st) in enumerate(entries)]}],
        "summary": {"total_files": 0,
                    "success": sum(1 for _, st in entries if st == "success"),
                    "failed": sum(1 for _, st in entries if st != "success"),
                    "needs_manual": 0},
    }
    path = raw_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _emit_collect_side_effect(collect_args, status: str = "success"):
    """模拟真实 collect_materials.py 的落盘副作用（drill log 写到本次采集的目标目录）。"""
    argv = [str(a) for a in collect_args]
    slug = argv[argv.index("--slug") + 1]
    if "--sources-json" in argv:
        sources = json.loads(argv[argv.index("--sources-json") + 1])
        inputs = [s.get("input", "") for s in sources]
    else:
        inputs = [argv[argv.index("--input") + 1]]
    dest = paths.raw_dir(slug, orchestrate.WORKSPACE)
    if "--dest-subdir" in argv:
        dest = dest / argv[argv.index("--dest-subdir") + 1]
    _write_drill_log(dest, [(inp, status) for inp in inputs])


def _script_fake(responses, drill_status: str = "success"):
    """run_script 替身：collect 调用顺带写出真实采集器会写的 drill log。

    收集子进程被 mock 后不会产生任何落盘副作用，不补这一步的话新的「缺料门禁」会把
    纯路由测试判成 MATERIALS_MISSING。
    """
    def _fake(script_name, args, timeout=120):
        if "collect_materials.py" in str(script_name):
            _emit_collect_side_effect(args, drill_status)
        return responses
    return _fake


def _mock_run(responses=None, drill_status="success"):
    if responses is None:
        responses = {
            "ok": True,
            "stdout": json.dumps({
                "task": "write article",
                "taskName": "brief-test",
                "model": "flash",
                "slug": "test",
                "output_path": "artifacts/test/test_brief.md",
                "has_brief": False,
            }),
            "stderr": "",
            "exit_code": 0,
        }
    return mock.patch.object(orchestrate, "run_script",
                            side_effect=_script_fake(responses, drill_status))


def _collect_calls(fake) -> list:
    return [c for c in fake.call_args_list if "collect_materials.py" in str(c[0][0])]


def _one_collect_args(fake):
    calls = _collect_calls(fake)
    return list(calls[0][0][1]) if calls else None


def _snapshot(root: Path) -> dict:
    """raw/ 下全部文件的 (相对路径 → (大小, mtime))，用于断言「没有新增/改动」。"""
    if not root.exists():
        return {}
    return {f.relative_to(root).as_posix(): (f.stat().st_size, f.stat().st_mtime_ns)
            for f in sorted(root.rglob("*")) if f.is_file()}


def _fetch_statuses(db_path: Path, slug: str) -> list:
    return [json.loads(e["detail"]).get("status")
            for e in wiki_index.get_events(db_path, slug=slug, action="FETCH")]


def _capture_json(captured):
    output = "".join(str(x) for x in captured)
    return json.loads(output)


def _seed_running_entry(db_path: Path, slug: str, **kwargs):
    """Insert a pending/running entry with ENQUEUE + STARTED events."""
    defaults = {"source_input": "https://arxiv.org/abs/2605.26112"}
    defaults.update(kwargs)
    seed_entry(
        db_path, slug,
        events=[("ENQUEUE", {"input": defaults["source_input"], "sources": 1}),
                ("STARTED", {"queued_at": None})],
        **defaults,
    )


class TestResolveInputs:
    """测试输入解析：重复 --input、--inputs-file、单条多 URL 拆分。"""

    def _resolve(self, **kwargs):
        defaults = {"input": None, "inputs_file": None}
        defaults.update(kwargs)
        return intake.resolve_inputs(
            input_list=defaults["input"],
            inputs_file=defaults["inputs_file"],
        )

    def test_repeated_inputs(self):
        inputs = self._resolve(input=["https://a.com", "keyword one", "https://b.com"])
        assert inputs == ["https://a.com", "keyword one", "https://b.com"]

    def test_inputs_file(self, tmp_path):
        f = tmp_path / "sources.txt"
        f.write_text("# comment\nhttps://arxiv.org/abs/2605.26112\n\nOpenAI\n", encoding="utf-8")
        inputs = self._resolve(inputs_file=str(f))
        assert inputs == ["https://arxiv.org/abs/2605.26112", "OpenAI"]

    def test_multi_url_in_one_string(self):
        inputs = self._resolve(input=["https://a.com https://b.com https://c.com"])
        assert inputs == ["https://a.com", "https://b.com", "https://c.com"]

    def test_url_plus_keyword_in_one_string(self):
        inputs = self._resolve(input=["https://a.com related keyword"])
        assert inputs == ["https://a.com", "related keyword"]

    def test_newline_in_input(self):
        inputs = self._resolve(input=["https://a.com\nkeyword"])
        assert inputs == ["https://a.com", "keyword"]


class TestClassifyAndPrimarySourceType:
    """测试逐源分类与主类型选择。"""

    def test_local_path_skips_classify(self):
        pdf = FIXTURES_DIR / "sample_paper.pdf"
        assert pdf.exists(), "fixture sample_paper.pdf must exist"
        c = intake.classify_one(str(pdf))
        assert c["source_type"] == "local"

    def test_arxiv_url_classification(self):
        c = intake.classify_one("https://arxiv.org/abs/2605.26112")
        assert c["source_type"] == "arxiv"

    def test_github_url_classification(self):
        c = intake.classify_one("https://github.com/openai/gym")
        assert c["source_type"] == "github"

    def test_primary_source_type_same(self):
        classifications = [{"source_type": "arxiv"}, {"source_type": "arxiv"}]
        assert intake.pick_primary_source_type(classifications) == "arxiv"

    def test_primary_source_type_diff_falls_back_to_multi_source(self):
        classifications = [{"source_type": "arxiv"}, {"source_type": "github"}]
        assert intake.pick_primary_source_type(classifications) == "multi_source"

    def test_primary_source_type_all_local(self):
        classifications = [{"source_type": "local"}]
        assert intake.pick_primary_source_type(classifications) == "local"


class TestRawDirHelpers:
    """测试 raw 目录内容检测。"""

    def test_empty_raw_dir(self, tmp_path, monkeypatch):
        raw = paths.raw_dir("slug", tmp_path)
        raw.mkdir(parents=True)
        monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp_path)
        assert orchestrate._raw_dir_has_content("slug") is False

    def test_meta_files_only_are_ignored(self, tmp_path, monkeypatch):
        raw = paths.raw_dir("slug", tmp_path)
        raw.mkdir(parents=True)
        (raw / "_drill_log.json").write_text("{}")
        (raw / "_fetch_results.json").write_text("{}")
        monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp_path)
        assert orchestrate._raw_dir_has_content("slug") is False

    def test_real_file_detected(self, tmp_path, monkeypatch):
        raw = paths.raw_dir("slug", tmp_path)
        raw.mkdir(parents=True)
        (raw / "paper.pdf").write_text("dummy")
        monkeypatch.setattr("scripts.paths.get_workspace", lambda _=None: tmp_path)
        assert orchestrate._raw_dir_has_content("slug") is True


class TestRunCommandRouting:
    """用 mock run_script 验证 orchestrate 调用 collect/interpret 的参数。"""

    def test_single_source_uses_flat_collect(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "test",
                            source_input="https://arxiv.org/abs/2605.26112")
        with _mock_run() as fake, mock.patch("builtins.print"):
            orchestrate.cmd_run(_make_args(id="test"))

        calls = fake.call_args_list
        collect_calls = [c for c in calls if "collect_materials.py" in str(c[0][0])]
        assert len(collect_calls) == 1
        collect_args = collect_calls[0][0][1]
        assert "--input" in collect_args
        assert "--sources-json" not in collect_args

    def test_multi_source_uses_collect_sources(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(
            paths.db_path(tmp_path), "test",
            source_input="https://arxiv.org/abs/2605.26112\nhttps://github.com/user/repo",
            input_type="keywords",
            source_type="multi_source",
            topic_type="observation",
        )
        with _mock_run() as fake, mock.patch("builtins.print"):
            orchestrate.cmd_run(_make_args(id="test"))

        calls = fake.call_args_list
        collect_calls = [c for c in calls if "collect_materials.py" in str(c[0][0])]
        assert len(collect_calls) == 1
        collect_args = collect_calls[0][0][1]
        assert "--sources-json" in collect_args

    def test_append_rejected_when_target_missing(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "base")
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="base", append_to="nonexistent"))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "ENTRY_NOT_FOUND"

    def test_reuse_existing_raw_skips_collect(self, tmp_path, monkeypatch):
        raw = paths.raw_dir("existing", tmp_path)
        _write_drill_log(raw, [("https://arxiv.org/abs/2605.26112", "success")])
        (raw / "paper.pdf").write_text("dummy")
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "existing")
        with _mock_run() as fake, mock.patch("builtins.print"):
            orchestrate.cmd_run(_make_args(id="existing"))

        calls = fake.call_args_list
        collect_calls = [c for c in calls if "collect_materials.py" in str(c[0][0])]
        assert len(collect_calls) == 0
        interp_calls = [c for c in calls if "generate_task.py" in str(c[0][0])]
        assert len(interp_calls) == 1

    def test_force_collect_runs_collect_even_if_raw_exists(self, tmp_path, monkeypatch):
        raw = paths.raw_dir("existing", tmp_path)
        _write_drill_log(raw, [("https://arxiv.org/abs/2605.26112", "success")])
        (raw / "paper.pdf").write_text("dummy")
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "existing")
        with _mock_run() as fake, mock.patch("builtins.print"):
            orchestrate.cmd_run(_make_args(id="existing", force_collect=True))

        calls = fake.call_args_list
        collect_calls = [c for c in calls if "collect_materials.py" in str(c[0][0])]
        assert len(collect_calls) == 1, "--force-collect 应强制重新收集"

    def test_json_output_contains_sources_count(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(
            paths.db_path(tmp_path), "test",
            source_input="https://a.com\nhttps://b.com",
            input_type="keywords",
            source_type="multi_source",
            topic_type="observation",
        )
        captured = []
        with _mock_run() as fake, mock.patch("builtins.print", captured.append):
            orchestrate.cmd_run(_make_args(id="test"))

        assert captured
        output = "".join(str(x) for x in captured)
        data = json.loads(output)
        assert data.get("sources_count") == 2
        assert "source_inputs" in data
        assert len(data["source_inputs"]) == 2


class TestRecordMode:
    """record 模式（v3.0 默认）：task_mode、publish_cmd 无 --depth、mode 解析规则。"""

    def _record_mock(self):
        responses = {
            "ok": True,
            "stdout": json.dumps({
                "task": "extract record", "taskName": "record-test", "model": "flash",
                "slug": "test", "output_path": "artifacts/test/record.json",
                "task_mode": "record",
            }),
            "stderr": "", "exit_code": 0,
        }
        return mock.patch.object(orchestrate, "run_script",
                                 side_effect=_script_fake(responses))

    def test_explicit_record_mode(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "test")
        captured = []
        with self._record_mock() as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="test", mode="record", depth=None))
        assert rc == 0
        interp_calls = [c for c in fake.call_args_list if "generate_task.py" in str(c[0][0])]
        assert len(interp_calls) == 1
        interp_args = interp_calls[0][0][1]
        assert "--mode" in interp_args and "record" in interp_args
        assert "--depth" not in interp_args
        data = _capture_json(captured)
        assert data["task_mode"] == "record"
        assert data["mode"] == "run"
        assert data["depth"] is None
        assert "--depth" not in data["publish_cmd"]
        assert data["output_path"].endswith("record.json")

    def test_default_mode_is_record(self, tmp_path, monkeypatch):
        """不给 --mode/--depth 时默认 record。"""
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "test")
        captured = []
        with self._record_mock() as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="test", mode=None, depth=None))
        assert rc == 0
        data = _capture_json(captured)
        assert data["task_mode"] == "record"

    def test_explicit_depth_deprecated_in_v31(self, tmp_path, monkeypatch):
        """显式 --depth 在 v3.1 返回 DEPRECATED_MODE。"""
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "test")
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="test", mode=None, depth="deep"))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "DEPRECATED_MODE"

    def test_append_rejected_when_target_missing(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        _seed_running_entry(paths.db_path(tmp_path), "test")
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="test", mode=None, depth=None, append_to="nonexistent"))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "ENTRY_NOT_FOUND"


class TestWorkflowGates:
    """v3.1：记录层门禁。"""

    def test_run_without_id_rejected(self):
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id=None))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "DEPRECATED_WORKFLOW"
        assert "add" in data.get("next_cmd", "")

    def test_run_depth_deprecated_v31(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        conftest.seed_entry(
            paths.db_path(tmp_path), "only_enqueue",
            events=[("ENQUEUE", {}), ("STARTED", {})],
        )
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="only_enqueue", depth="brief"))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "DEPRECATED_MODE"

    def test_run_without_started_rejected(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        seed_entry(paths.db_path(tmp_path), "only_enqueue", events=[("ENQUEUE", {})])
        captured = []
        with mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="only_enqueue"))
        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "WORKFLOW_BYPASSED"
        assert "STARTED" in data["message"]

    def test_run_after_pop_succeeds(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        seed_entry(
            paths.db_path(tmp_path), "popped",
            events=[("ENQUEUE", {}), ("STARTED", {})],
        )
        captured = []
        with _mock_run({
            "ok": True,
            "stdout": json.dumps({
                "task": "t", "taskName": "tn", "model": "flash",
                "slug": "popped", "output_path": "artifacts/popped/popped_brief.md",
            }),
            "stderr": "", "exit_code": 0,
        }) as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="popped"))

        assert rc == 0
        interp_calls = [c for c in fake.call_args_list if "generate_task.py" in str(c[0][0])]
        assert len(interp_calls) == 1
        data = _capture_json(captured)
        assert data["slug"] == "popped"


class TestAddWorkflow:
    """验证 add 承担 intake 职责：多源解析、分类、ENQUEUE 事件。"""

    def _make_add_args(self, **kwargs):
        defaults = {
            "input": None,
            "inputs_file": None,
            "source_prompt": None,
            "append_to": None,
            "input_type": "unknown",
            "source_type": "unknown",
            "depth": "brief",
            "id": None,
            "json": True,
        }
        defaults.update(kwargs)
        return Namespace(**defaults)

    def test_add_multisource_records_enqueue_and_classifies(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        with mock.patch("builtins.print"):
            args = self._make_add_args(input=[
                "https://arxiv.org/abs/2605.26112",
                "https://github.com/user/repo",
            ])
            intake_cmds.cmd_add(args, paths.db_path(tmp_path))

        entries = wiki_index.list_entries(paths.db_path(tmp_path))
        assert len(entries) == 1
        entry = wiki_index.get_entry(paths.db_path(tmp_path), entries[0]["id"])
        assert entry is not None
        assert entry["source_type"] == "multi_source"
        assert entry["input_type"] == "url"
        assert entry["topic_type"] == "observation"
        assert entry["status"] == "pending"
        events = wiki_index.get_events(paths.db_path(tmp_path), slug=entry["id"], action="ENQUEUE")
        assert len(events) == 1
        detail = json.loads(events[0]["detail"])
        assert detail["sources"] == 2

    def test_add_append_requeues_and_records_enqueue(self, tmp_path, monkeypatch):
        _patch_db_paths(tmp_path, monkeypatch)
        with mock.patch("builtins.print"):
            wiki_index.upsert_task(
                paths.db_path(tmp_path), "base_done",
                source_input="old source",
                source_prompt="old source",
                input_type="url",
                source_type="arxiv",
                topic_type="paper",
                depth="brief",
                status="done",
            )
            args = self._make_add_args(
                append_to="base_done",
                input=["https://github.com/new-org/vla-code"],
            )
            intake_cmds.cmd_add(args, paths.db_path(tmp_path))

        entry = wiki_index.get_entry(paths.db_path(tmp_path), "base_done")
        assert entry["status"] == "pending"
        events = wiki_index.get_events(paths.db_path(tmp_path), slug="base_done", action="ENQUEUE")
        assert len(events) == 1
        assert "github.com/new-org/vla-code" in entry["source_input"]

    def test_add_append_rejected_for_unpublished_base(self, tmp_path, monkeypatch):
        """2026-07-20_001：append 只对已发布条目成立，非 done 条目必须被拒且状态不变。"""
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        for slug, status in (("base_pending", "pending"), ("base_running", "running")):
            wiki_index.upsert_task(
                db, slug,
                source_input="old source", source_prompt="old source",
                input_type="url", source_type="arxiv", topic_type="paper",
                depth="brief", status=status,
            )
            captured = []
            with mock.patch("builtins.print", captured.append), pytest.raises(SystemExit) as exc:
                intake_cmds.cmd_add(self._make_add_args(
                    append_to=slug, input=["https://github.com/new-org/vla-code"]), db)
            assert exc.value.code == 1
            data = _capture_json(captured)
            assert data["error"] == "APPEND_REQUIRES_PUBLISHED"
            assert status in data["message"]
            assert "首次 add" in data["message"]
            entry = wiki_index.get_entry(db, slug)
            assert entry["status"] == status
            assert entry["source_input"] == "old source"
            assert wiki_index.get_events(db, slug=slug, action="ENQUEUE") == []


class TestPublishWorkflowGate:
    """验证 Publisher 门禁：必须有 WRITE，缺失更早事件仅告警。"""

    def _make_publish_args(self, **kwargs):
        defaults = {"id": "x", "depth": "brief", "spec": None, "title": None, "json": True}
        defaults.update(kwargs)
        return Namespace(**defaults)

    def test_publish_legacy_article_with_depth(self, tmp_path, monkeypatch):
        """v3.1：publish --depth brief 走历史文章路径（不验证，只标记 done）。"""
        _patch_db_paths(tmp_path, monkeypatch)
        slug = "2026-06-30_legacy"
        wiki_index.upsert_task(
            paths.db_path(tmp_path), slug,
            source_input="https://a.com", topic_type="paper", status="running",
        )
        article = paths.article_path(slug, "brief", tmp_path)
        article.parent.mkdir(parents=True, exist_ok=True)
        article.write_text("# Legacy Article\n**Ver:** 1.0-brief\n")
        captured = []
        with mock.patch("builtins.print", captured.append):
            publish_cmds.cmd_publish(
                self._make_publish_args(id=slug, depth="brief"),
                paths.db_path(tmp_path), tmp_path, SCRIPT_DIR,
            )
        data = json.loads(captured[-1])
        assert data.get("ok")
        assert data["id"] == slug
        entry = wiki_index.get_entry(paths.db_path(tmp_path), slug)
        assert entry["status"] == "done"


# Record publish tests are covered in test_records_publish.py


# ============================================================
# 2026-09-15_001 / 2026-07-20_001：append 补料 + 缺料门禁 + 可观测性
# ============================================================

class TestAppendMaterialFlow:
    """append 补料必须抓新来源；声明来源缺料必须失败而不是静默放行。"""

    BASE = "2026-09-15_5365"
    NEW_SOURCE = "https://arxiv.org/abs/2607.08448"
    OLD_SOURCE = "https://mp.weixin.qq.com/s/old-article"

    def _seed_published_base(self, tmp_path, slug, record=True, done_event=True):
        """base：raw/ 有旧材料 + drill log 记录旧来源 success + 已发布证据（record.json / DONE）。"""
        raw = paths.raw_dir(slug, tmp_path)
        _write_drill_log(raw, [(self.OLD_SOURCE, "success")])
        (raw / "old_material.html").write_text("old material", encoding="utf-8")
        if record:
            rec = paths.record_path(slug, tmp_path)
            rec.parent.mkdir(parents=True, exist_ok=True)
            rec.write_text("{}", encoding="utf-8")
        if done_event:
            wiki_index.record_event(paths.db_path(tmp_path), slug, "DONE", {"file": "record.json"})

    def test_append_collects_new_source_into_append_dir(self, tmp_path, monkeypatch):
        """真实复现路径：run --id <base>（不带 --append-to），append 意图只在 ENQUEUE 事件里。"""
        slug = self.BASE
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        self._seed_published_base(tmp_path, slug)
        seed_entry(
            db, slug,
            source_input=self.NEW_SOURCE, source_type="arxiv",
            events=[("ENQUEUE", {"input": self.NEW_SOURCE, "append_to": slug}),
                    ("STARTED", {"queued_at": None})],
        )

        with _mock_run() as fake, mock.patch("builtins.print"):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 0
        collect_args = _one_collect_args(fake)
        assert collect_args is not None, "append 必须触发素材收集"
        assert collect_args[collect_args.index("--dest-subdir") + 1] == "append_1"
        raw = paths.raw_dir(slug, tmp_path)
        assert (raw / "append_1" / "_drill_log.json").exists()
        assert (raw / "old_material.html").read_text(encoding="utf-8") == "old material"
        assert _fetch_statuses(db, slug) == ["success"]
        assert "skipped (reuse existing raw)" not in _fetch_statuses(db, slug)

    def test_append_uses_next_free_index(self, tmp_path, monkeypatch):
        """已有 raw/append_1/ 时新一次补料落 raw/append_2/，不覆盖前一次。"""
        slug = "2026-09-15_5365b"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        self._seed_published_base(tmp_path, slug)
        (paths.raw_dir(slug, tmp_path) / "append_1").mkdir(parents=True, exist_ok=True)
        seed_entry(
            db, slug, source_input=self.NEW_SOURCE, source_type="arxiv",
            events=[("ENQUEUE", {"input": self.NEW_SOURCE, "append_to": slug}), ("STARTED", {})],
        )

        with _mock_run() as fake, mock.patch("builtins.print"):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 0
        collect_args = _one_collect_args(fake)
        assert collect_args[collect_args.index("--dest-subdir") + 1] == "append_2"

    def test_rerun_with_nothing_new_reuses_raw(self, tmp_path, monkeypatch):
        """同一来源重跑：复用既有 raw/，不重复下载、不新增文件。"""
        slug = "reuse_after_append"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        raw = paths.raw_dir(slug, tmp_path)
        # 声称已抓取的 URL 带 query 参数且 host 大小写不同 —— 归一化后仍应命中复用
        _write_drill_log(raw, [("https://arXiv.org/abs/2605.26112?utm_source=x", "success")])
        (raw / "paper.pdf").write_text("dummy", encoding="utf-8")
        before = _snapshot(raw)
        seed_entry(db, slug, events=[("ENQUEUE", {"input": "https://arxiv.org/abs/2605.26112"}),
                                     ("STARTED", {})])

        with _mock_run() as fake, mock.patch("builtins.print"):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 0
        assert _one_collect_args(fake) is None
        assert _snapshot(raw) == before
        assert _fetch_statuses(db, slug) == ["skipped (reuse existing raw)"]

    def test_declared_source_without_material_blocks_run(self, tmp_path, monkeypatch):
        """声明来源 drill log 为 failed → MATERIALS_MISSING，且不生成提取任务。"""
        slug = "missing_material"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        _seed_running_entry(db, slug)
        captured = []
        with _mock_run(drill_status="failed") as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "MATERIALS_MISSING"
        assert data["detail"]["missing"] == [
            {"url": "https://arxiv.org/abs/2605.26112", "status": "failed"}]
        assert _one_collect_args(fake) is not None
        assert _collect_calls(fake)
        assert [c for c in fake.call_args_list if "generate_task.py" in str(c[0][0])] == []
        assert "failed" in _fetch_statuses(db, slug)
        assert not wiki_index.get_entry(db, slug).get("materials_ready")

    def test_missing_material_visible_in_non_json_mode(self, tmp_path, monkeypatch, capsys):
        """非 JSON 模式：同样的失败以 stderr 上的 MATERIALS_MISSING 呈现，退出码非 0。"""
        slug = "missing_non_json"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        _seed_running_entry(db, slug)
        with _mock_run(drill_status="failed"):
            rc = orchestrate.cmd_run(_make_args(id=slug, json=False, quiet=False))

        assert rc == 1
        captured = capsys.readouterr()
        assert "MATERIALS_MISSING" in captured.err
        assert "https://arxiv.org/abs/2605.26112" in captured.err
        assert not wiki_index.get_entry(db, slug).get("materials_ready")

    def test_accept_manual_downgrades_missing_to_warning(self, tmp_path, monkeypatch):
        """--accept-manual：缺料降级为告警（手工抓取场景），run 继续。"""
        slug = "manual_ok"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        _seed_running_entry(db, slug)
        captured = []
        with _mock_run(drill_status="failed") as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id=slug, accept_manual=True))

        assert rc == 0
        assert _capture_json(captured)["slug"] == slug
        assert _fetch_statuses(db, slug) == ["manual (accepted)"]
        assert [c for c in fake.call_args_list if "generate_task.py" in str(c[0][0])]
        assert wiki_index.get_entry(db, slug).get("materials_ready")

    def test_run_append_rejected_when_base_not_done(self, tmp_path, monkeypatch):
        """2026-07-20_001：run --append-to 指向非 done 的其它条目 → APPEND_REQUIRES_PUBLISHED。"""
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        seed_entry(db, "other_base", events=[("ENQUEUE", {})])
        seed_entry(db, "child", events=[("ENQUEUE", {}), ("STARTED", {})])
        captured = []
        with _mock_run() as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id="child", append_to="other_base"))

        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "APPEND_REQUIRES_PUBLISHED"
        assert "pending" in data["message"]
        assert _one_collect_args(fake) is None

    def test_self_append_requires_published_record(self, tmp_path, monkeypatch):
        """自 append（append_to == 自身）没有已发布证据时拒绝，而不是跑出不可能的状态。"""
        slug = "2026-09-15_unpublished"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        seed_entry(db, slug, events=[("ENQUEUE", {"append_to": slug}), ("STARTED", {})])
        captured = []
        with _mock_run() as fake, mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 1
        data = _capture_json(captured)
        assert data["error"] == "APPEND_REQUIRES_PUBLISHED"
        assert "没有已发布记录" in data["message"]
        assert _one_collect_args(fake) is None

    def test_interpret_failure_keeps_last_stderr_line(self, tmp_path, monkeypatch):
        """2026-07-20_001：DB error 保留 traceback 最后一行（异常类型 + 消息）。"""
        slug = "boom"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        raw = paths.raw_dir(slug, tmp_path)
        _write_drill_log(raw, [("https://arxiv.org/abs/2605.26112", "success")])
        (raw / "paper.pdf").write_text("dummy", encoding="utf-8")
        _seed_running_entry(db, slug)
        stderr = ('Traceback (most recent call last):\n'
                  '  File "generate_task.py", line 71, in <module>\n'
                  '    main()\n'
                  'ValueError: boom during record extraction')
        captured = []
        with _mock_run({"ok": False, "stdout": "", "stderr": stderr, "exit_code": 1}) as fake, \
                mock.patch("builtins.print", captured.append):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 1
        entry = wiki_index.get_entry(db, slug)
        assert entry["status"] == "failed"
        assert entry["error"] == "interpret failed: ValueError: boom during record extraction"
        assert "Traceback" not in entry["error"]

    def test_interpret_failure_error_field_truncated_to_200(self, tmp_path, monkeypatch):
        slug = "boom_long"
        _patch_db_paths(tmp_path, monkeypatch)
        db = paths.db_path(tmp_path)
        raw = paths.raw_dir(slug, tmp_path)
        _write_drill_log(raw, [("https://arxiv.org/abs/2605.26112", "success")])
        (raw / "paper.pdf").write_text("dummy", encoding="utf-8")
        _seed_running_entry(db, slug)
        stderr = "Traceback (most recent call last):\nValueError: " + "x" * 400
        with _mock_run({"ok": False, "stdout": "", "stderr": stderr, "exit_code": 1}), \
                mock.patch("builtins.print"):
            rc = orchestrate.cmd_run(_make_args(id=slug))

        assert rc == 1
        error = wiki_index.get_entry(db, slug)["error"]
        assert len(error) == 200
        assert error.startswith("interpret failed: ValueError: xxx")


class TestNextAppendIndex:
    """_next_append_index：raw/append_* 取 max+1，无既有子目录时为 1。"""

    def test_no_append_dirs_yet(self, tmp_path):
        assert orchestrate._next_append_index(tmp_path / "missing") == 1
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "_drill_log.json").write_text("{}", encoding="utf-8")
        assert orchestrate._next_append_index(raw) == 1

    def test_returns_max_plus_one(self, tmp_path):
        raw = tmp_path / "raw"
        for name in ("append_1", "append_2", "other"):
            (raw / name).mkdir(parents=True, exist_ok=True)
        (raw / "append_2" / "_drill_log.json").write_text("{}", encoding="utf-8")
        assert orchestrate._next_append_index(raw) == 3
