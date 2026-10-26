"""状态机测试：转移、守卫、快照、事件。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield.web.state import AppState  # noqa: E402


class TestDataState(unittest.TestCase):
    def setUp(self):
        self.s = AppState()

    def test_idle_to_building_to_ready(self):
        self.assertEqual(self.s.data.state, "idle")
        self.assertTrue(self.s.begin_build())
        self.assertEqual(self.s.data.state, "building")
        self.s.build_progress(42, "下载中")
        self.assertEqual(self.s.data.percent, 42)
        self.s.build_done("v1")
        self.assertEqual(self.s.data.state, "ready")
        self.assertEqual(self.s.data.version, "v1")
        self.assertEqual(self.s.data.percent, 100)
        self.assertTrue(self.s.data_ready())

    def test_no_double_build(self):
        self.assertTrue(self.s.begin_build())
        self.assertFalse(self.s.begin_build())
        self.assertEqual(self.s.data.state, "building")

    def test_build_error_then_retry(self):
        self.s.begin_build()
        self.s.build_failed("boom")
        self.assertEqual(self.s.data.state, "error")
        self.assertFalse(self.s.data_ready())
        self.assertIn("boom", self.s.data.error)
        self.assertTrue(self.s.begin_build())
        self.assertEqual(self.s.data.state, "building")

    def test_progress_ignored_when_not_building(self):
        self.s.build_progress(50, "x")   # idle 状态应忽略
        self.assertEqual(self.s.data.percent, 0)

    def test_snapshot_shape(self):
        self.s.begin_build()
        self.s.build_done("v2")
        snap = self.s.snapshot()
        self.assertEqual(snap["data"]["state"], "ready")
        self.assertEqual(snap["data"]["version"], "v2")
        self.assertIn("schema_version", snap)
        self.assertIn("llm", snap)
        self.assertEqual(snap["llm"]["state"], "idle")


class TestLLMState(unittest.TestCase):
    def setUp(self):
        self.s = AppState()

    def test_requires_ready_data(self):
        with self.assertRaises(RuntimeError):
            self.s.begin_llm()
        self.s.begin_build()
        with self.assertRaises(RuntimeError):
            self.s.begin_llm()
        self.s.build_done("v1")
        self.assertTrue(self.s.begin_llm())
        self.assertEqual(self.s.llm.state, "running")

    def test_single_run(self):
        self.s.begin_build()
        self.s.build_done("v1")
        self.assertTrue(self.s.begin_llm())
        self.assertFalse(self.s.begin_llm())

    def test_done_and_error(self):
        self.s.begin_build()
        self.s.build_done("v1")
        self.s.begin_llm()
        self.s.llm_progress("复核中")
        self.s.llm_done({"llm_used": 3})
        self.assertEqual(self.s.llm.state, "done")
        self.assertEqual(self.s.llm.stats["llm_used"], 3)
        self.s.begin_llm()
        self.s.llm_failed("quota")
        self.assertEqual(self.s.llm.state, "error")
        self.assertIn("quota", self.s.llm.error)

    def test_llm_done_invalidates_dataset_cache(self):
        self.s.begin_build()
        self.s.build_done("v1")
        self.s._cache.update(ds=object(), ts=1.0)
        self.s.begin_llm()
        self.s.llm_done({})
        self.assertIsNone(self.s._cache["ds"])


class TestEvents(unittest.TestCase):
    def test_event_log(self):
        s = AppState()
        s.begin_build()
        s.build_done("v1")
        kinds = [e["kind"] for e in s.snapshot()["events"]]
        self.assertIn("build.start", kinds)
        self.assertIn("build.done", kinds)

    def test_mark_ready_reuses_data(self):
        s = AppState()
        s.mark_ready("cached")
        self.assertTrue(s.data_ready())
        self.assertEqual(s.data.version, "cached")
        self.assertEqual(s.data.percent, 100)


if __name__ == "__main__":
    unittest.main()
