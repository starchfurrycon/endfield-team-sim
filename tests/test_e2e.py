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


class TestAutoAttack(unittest.TestCase):
    """主控以外的干员会自动普攻（wiki_tut_bat_team_1）。"""

    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()
        cls.ids = ["chr_0005_chen", "chr_0021_whiten", "chr_0022_bounda",
                   "chr_0028_wulfa"]

    def _run(self, auto: bool) -> object:
        from endfield.engine.battle import Battle
        b = Battle(self.ds, Scenario(team=self.ids, duration_s=20,
                                     offfield_auto_attack=auto))
        b.advance_to(b.end)          # 主控什么都不做
        return b.result()

    def test_offfield_deals_damage(self):
        res = self._run(True)
        self.assertGreater(res.total_damage, 0)
        self.assertGreater(res.stats["cast_counts"].get("auto_attack", 0), 0)

    def test_disabled_means_no_damage(self):
        res = self._run(False)
        self.assertEqual(res.total_damage, 0)

    def test_offfield_heavy_gives_no_sp(self):
        """只有主控的重击回技力，自动普攻不应该额外给技力。"""
        from endfield.engine.battle import Battle
        b = Battle(self.ds, Scenario(team=self.ids, duration_s=20))
        start = b.sp
        b.advance_to(b.end)
        regen_cap = start + 20 * config.guess("SP_REGEN_PER_SEC")
        self.assertLessEqual(b.sp, regen_cap + 1e-6)


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

