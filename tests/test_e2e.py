"""端到端 / 约束 / 单调性回归测试。

这些是"产品承诺"级别的测试：
  * 禁止的人不能出现在推荐里
  * 必须的人一定要在推荐里
  * 终结技能量满不可能比能量 0 打得更少（单调性）
  * 本地 API 返回的字段与 UI 期望一致
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                     # noqa: E402
from endfield.model import Dataset              # noqa: E402
from endfield.engine.battle import Scenario     # noqa: E402
from endfield.search.rotations import search_pair  # noqa: E402
from endfield.search.teams import TeamQuery, recommend  # noqa: E402


def _ds() -> Dataset:
    return Dataset.load(config.NORM)


class TestConstraints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()

    def test_ban_is_respected(self):
        res = recommend(self.ds, TeamQuery(must=["陈千语"], ban=["萤石"],
                                           top_k=3, evaluate=4,
                                           duration_s=25), verbose=False)
        self.assertTrue(res["top"], "应至少给出一队")
        for t in res["top"]:
            self.assertNotIn("chr_0022_bounda", t["team"])

    def test_must_is_respected(self):
        res = recommend(self.ds, TeamQuery(must=["陈千语"], top_k=3,
                                           evaluate=4, duration_s=25),
                        verbose=False)
        for t in res["top"]:
            self.assertIn("chr_0005_chen", t["team"])

    def test_team_size(self):
        res = recommend(self.ds, TeamQuery(must=["陈千语"], top_k=2,
                                           evaluate=2, duration_s=20),
                        verbose=False)
        for t in res["top"]:
            self.assertEqual(len(t["team"]), 4)
            self.assertEqual(len(set(t["team"])), 4, "不能有重复角色")

    def test_unknown_name_reported(self):
        res = recommend(self.ds, TeamQuery(must=["不存在的干员"], top_k=1,
                                           evaluate=1, duration_s=20),
                        verbose=False)
        self.assertIn("不存在的干员", res["missing_names"])


class TestMainControl(unittest.TestCase):
    """只有主控能普攻/重击；换人是显式动作（docs/MECHANICS.md）。"""

    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()
        cls.ids = ["chr_0005_chen", "chr_0021_whiten", "chr_0022_bounda",
                   "chr_0028_wulfa"]

    def _battle(self):
        from endfield.engine.battle import Battle
        return Battle(self.ds, Scenario(team=self.ids, duration_s=20))

    def test_no_offfield_auto_attack(self):
        b = self._battle()
        b.advance_to(b.end)          # 主控什么都不做
        res = b.result()
        self.assertEqual(res.total_damage, 0)
        self.assertEqual(res.stats["cast_counts"].get("auto_attack", 0), 0)

    def test_attacks_only_for_main(self):
        b = self._battle()
        acts = b.legal_actions(include_wait=False)
        attacks = [a for a in acts if a.kind == "attack"]
        self.assertTrue(attacks)
        self.assertTrue(all(a.op == b.main for a in attacks))
        self.assertTrue(any(a.kind == "switch" for a in acts))

    def test_switch_changes_main(self):
        from endfield.engine.battle import Action
        b = self._battle()
        old = b.main
        other = next(cid for cid in self.ids if cid != old)
        t0 = b.t
        b.apply(Action("switch", other))
        self.assertEqual(b.main, other)
        self.assertGreater(b.t, t0)
        self.assertGreaterEqual(
            (b.result().stats.get("cast_counts") or {}).get("switch", 0), 1)

    def test_new_main_can_heavy(self):
        from endfield.engine.battle import Action
        b = self._battle()
        old = b.main
        other = next(cid for cid in self.ids if cid != old)
        b.apply(Action("switch", other))
        for _ in range(8):
            if b.t >= b.end:
                break
            b.apply(Action("attack", other))
        res = b.result()
        heavy = [ev for ev in res.timeline if ev.get("slot") == "power_attack"]
        self.assertTrue(heavy)
        self.assertTrue(all(ev.get("op") == other for ev in heavy))

    def test_heavy_gives_sp(self):
        from endfield.engine.battle import Action
        b = self._battle()
        for _ in range(8):
            if b.t >= b.end:
                break
            before = b.sp
            b.apply(Action("attack", b.main))
            heavy = [ev for ev in b.log if ev.get("slot") == "power_attack"]
            if heavy:
                self.assertGreater(b.sp, before - 1e-6)
                return
        self.fail("main never reached a heavy attack")


class TestMonotonicity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()

    def test_hot_not_worse_than_cold(self):
        ids = ["chr_0005_chen", "chr_0021_whiten", "chr_0022_bounda",
               "chr_0028_wulfa"]
        cold, hot = search_pair(
            self.ds,
            Scenario(team=ids, duration_s=30, start_energy="cold"),
            Scenario(team=ids, duration_s=30, start_energy="hot"))
        self.assertGreaterEqual(hot.result.total_damage,
                                cold.result.total_damage,
                                f"cold={cold.result.total_damage} "
                                f"hot={hot.result.total_damage}")


class TestLocalAPI(unittest.TestCase):
    def test_meta_shape(self):
        from endfield.web import server
        m = server.meta()
        self.assertIn("operators", m)
        self.assertGreater(len(m["operators"]), 10)
        for op in m["operators"][:5]:
            for key in ("id", "name", "rarity", "element"):
                self.assertIn(key, op)
        self.assertIn("version", m)

    def test_axis_payload(self):
        from endfield.web import server
        data = server.axis({"team": ["chr_0005_chen", "chr_0021_whiten"],
                            "mode": "cold", "duration": 15})
        res = data["rotation"]["result"]
        self.assertIn("timeline", res)
        for ev in res["timeline"]:
            self.assertIn("frame", ev)
            self.assertIn("kind", ev)


class TestReports(unittest.TestCase):
    def test_reports_exist(self):
        for name in ("coverage.md", "crosscheck.md", "semantics.md",
                     "constants.md"):
            p = config.REPORTS / name
            self.assertTrue(p.exists(), f"缺少报告 {name}")

    def test_manifest_has_source_refs(self):
        import json
        m = json.loads((config.NORM / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue((m.get("github") or {}).get("sha"))
        self.assertTrue((m.get("wiki") or {}).get("generator"))


if __name__ == "__main__":
    unittest.main()

