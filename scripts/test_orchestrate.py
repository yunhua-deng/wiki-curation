#!/usr/bin/env python3
"""
skills/wiki-curation/scripts/test_orchestrate.py — orchestrate.py / CLI 编排与工作流门禁测试。

设计原则：
- 使用 pytest fixtures 替代 unittest shim。
- 需要网络/LLM/外部工具的测试标记 @require_ci，默认跳过。
- 核心路径（输入解析、分类聚合、收集路由、追加输出路径、deep 复用 raw、工作流门禁）用 mock 验证。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

from scripts import conftest
from scripts import intake
from scripts import paths
from scripts import wiki_index
from scripts.conftest import SCRIPT_DIR, require_ci, seed_entry
from scripts.exec import orchestrate
from scripts.intake import commands as intake_cmds
from scripts.publish import commands as publish_cmds


FIXTURES_DIR = SCRIPT_DIR.parent / "tests" / "fixtures"
ORCHESTRATE_CASES = FIXTURES_DIR / "orchestrate_cases.json"


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


def _mock_run(responses=None):
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
    return mock.patch.object(orchestrate, "run_script", return_value=responses)


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
        raw.mkdir(parents=True)
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
        raw.mkdir(parents=True)
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
        return mock.patch.object(orchestrate, "run_script", return_value={
            "ok": True,
            "stdout": json.dumps({
                "task": "extract record", "taskName": "record-test", "model": "flash",
                "slug": "test", "output_path": "artifacts/test/record.json",
                "task_mode": "record",
            }),
            "stderr": "", "exit_code": 0,
        })

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
        with mock.patch.object(orchestrate, "run_script", return_value={
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


class TestFixturesLoaded:
    """验证 fixtures/orchestrate_cases.json 可加载且结构正确。"""

    def test_fixture_loads(self):
        assert ORCHESTRATE_CASES.exists()
        data = json.loads(ORCHESTRATE_CASES.read_text(encoding="utf-8"))
        assert "cases" in data
        names = {c["name"] for c in data["cases"]}
        expected = {
            "single_arxiv_url",
            "multiple_urls_combined",
            "url_plus_keyword",
            "github_repo_deep",
            "local_file",
            "append_to_existing",
        }
        assert expected.issubset(names), f"missing cases: {expected - names}"


class TestCLIDeepReuse:
    """集成测试：CLI --id + --depth deep 复用已有 raw，不触发网络收集。"""

    def _make_tmp_workspace(self):
        repo_ws = SCRIPT_DIR.parent
        tmp = Path(tempfile.mkdtemp(prefix="wiki_test_ws_"))
        shutil.copytree(repo_ws / "references", tmp / "references", dirs_exist_ok=True)
        shutil.copytree(repo_ws / "assets", tmp / "assets", dirs_exist_ok=True)
        (tmp / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
        return tmp

    @require_ci
    def test_cli_deep_reuse_raw(self):
        tmp = self._make_tmp_workspace()
        slug = f"test_deep_reuse_{os.getpid()}"
        raw = paths.raw_dir(slug, tmp)
        raw.mkdir(parents=True)
        (raw / "_fetch_results.json").write_text(json.dumps({"results": []}))
        (raw / "paper.html").write_text("<html>paper</html>")

        env = os.environ.copy()
        env["WIKI_WORKSPACE"] = str(tmp)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["PYTHONPATH"] = str(SCRIPT_DIR.parent) + os.pathsep + env.get("PYTHONPATH", "")

        add_cmd = [
            sys.executable, str(SCRIPT_DIR / "wiki_db.py"),
            "--json", "add",
            "--id", slug,
            "--input", "https://arxiv.org/abs/2605.26112",
            "--type", "url",
            "--subtype", "arxiv_paper",
            "--depth", "deep",
        ]
        r = subprocess.run(add_cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode == 0, f"add failed: {r.stderr}\nstdout:\n{r.stdout}"

        pop_cmd = [
            sys.executable, str(SCRIPT_DIR / "wiki_db.py"),
            "--json", "pop",
        ]
        r = subprocess.run(pop_cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode == 0, f"pop failed: {r.stderr}\nstdout:\n{r.stdout}"

        run_cmd = [
            sys.executable, str(SCRIPT_DIR / "cli.py"),
            "--json", "run",
            "--id", slug,
            "--depth", "deep",
            "--no-download-zip",
        ]
        r = subprocess.run(run_cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        assert r.returncode == 0, f"run failed: {r.stderr}\nstdout:\n{r.stdout}"
        data = json.loads(r.stdout)
        assert data.get("ok", True)
        payload = data.get("data", {})
        assert payload.get("depth") == "deep"
        assert payload.get("slug") == slug
        assert payload.get("sources_count") == 1
        assert "source_inputs" in payload
