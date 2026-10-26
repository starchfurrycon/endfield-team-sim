"""UI 层契约测试（v2.3.0）。

覆盖三件事，都是前端直接依赖的：
  * 水位采样：首尾齐全、上限正确（画曲线要用的数据）
  * 装备/武器预设：真的进了 Scenario，且伤害方向正确
  * 对比接口：两条方案的差值可复现、同配置差值为 0
另外守住敌人节奏模板（每次出手都按模板，而不是只有第一次）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                       # noqa: E402
from endfield.engine.battle import Battle, Scenario  # noqa: E402
from endfield.model import Dataset                # noqa: E402
from endfield.web import server                   # noqa: E402

TEAM = ["chr_0005_chen", "chr_0021_whiten"]


def _ds() -> Dataset:
    return Dataset.load(config.NORM)


class TestWaterSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = server.axis({"team": TEAM, "mode": "cold", "duration": 15})

    def test_samples_cover_window(self):
        res = self.data["rotation"]["result"]
        samples = res["stats"]["samples"]
        self.assertGreater(len(samples), 20)
        self.assertEqual(samples[0]["f"], 0, "曲线必须从 0 帧开始")
        self.assertEqual(samples[-1]["f"], res["duration_frames"],
                         "曲线必须到窗口结束")

    def test_caps_and_energy_keys(self):
        res = self.data["rotation"]["result"]
        st = res["stats"]
        self.assertEqual(st["sp_max"], 300.0)
        self.assertEqual(set(st["energy_cap"]), set(TEAM))
        for cid, cap in st["energy_cap"].items():
            self.assertGreater(cap, 0, cid)
        for s in st["samples"]:
            self.assertLessEqual(s["sp"], st["sp_max"] + 1e-6)
            for cid in TEAM:
                self.assertIn(cid, s["e"])
                self.assertLessEqual(s["e"][cid], st["energy_cap"][cid] + 1e-6)

    def test_hot_starts_full(self):
        hot = server.axis({"team": TEAM, "mode": "hot", "duration": 10})
        st = hot["rotation"]["result"]["stats"]
        first = st["samples"][0]["e"]
        for cid, cap in st["energy_cap"].items():
            self.assertAlmostEqual(first[cid], cap, places=1)


class TestLoadoutPresets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = server.axis({"team": TEAM, "mode": "cold", "duration": 18})
        cls.gear = server.axis({"team": TEAM, "mode": "cold", "duration": 18,
                                "equip": "output", "equip_level": 3})
        cls.wpn = server.axis({"team": TEAM, "mode": "cold", "duration": 18,
                               "weapon": 1})

    def test_equipment_goes_into_scenario(self):
        cfg = self.gear["config"]
        self.assertEqual(len(cfg["equip_by_op"]), len(TEAM))
        for name, pieces in cfg["equip_by_op"].items():
            self.assertEqual(len(pieces), 4, name)
        self.assertTrue(any("装备" in n for n in cfg["notes"]))

    def test_equipment_raises_damage_without_exploding(self):
        a = self.base["rotation"]["result"]["total_damage"]
        b = self.gear["rotation"]["result"]["total_damage"]
        self.assertGreater(b, a, "输出向套装应该提升伤害")
        # 合理性上界：4 件满精锻装备不该把伤害翻三倍
        self.assertLess(b / a, 3.0, f"装备加成疑似过量：{a} → {b}")

    def test_weapon_preset_changes_weapon(self):
        st = self.wpn["rotation"]["result"]["stats"]
        self.assertTrue(st["weapons"])
        default_ids = {cid: _ds().operators[cid].weapon.get("id") for cid in TEAM}
        self.assertNotEqual(st["weapons"], default_ids,
                            "换了武器预设，结果里的武器应随之改变")
        self.assertGreater(self.wpn["rotation"]["result"]["total_damage"],
                           self.base["rotation"]["result"]["total_damage"])

    def test_no_preset_keeps_old_behaviour(self):
        cfg = self.base["config"]
        self.assertEqual(cfg["equip_by_op"], {})
        self.assertEqual(cfg["weapon_by_op"], {})
        self.assertTrue(any("无装备" in n for n in cfg["notes"]))


class TestCompare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.same = server.compare({
            "team": TEAM, "duration": 18,
            "a": {"mode": "cold", "enemy": "normal", "equip": "none", "weapon": 0},
            "b": {"mode": "cold", "enemy": "normal", "equip": "none", "weapon": 0},
        })
        cls.diff = server.compare({
            "team": TEAM, "duration": 18,
            "a": {"mode": "cold", "enemy": "normal", "equip": "none", "weapon": 0},
            "b": {"mode": "cold", "enemy": "normal", "equip": "output", "weapon": 1},
        })

    def test_sides_have_axes_and_config(self):
        for side in ("a", "b"):
            s = self.same["sides"][side]
            self.assertIn("rotation", s)
            self.assertIn("timeline", s["rotation"]["result"])
            self.assertIn("notes", s["config"])
            self.assertTrue(s["label"])

    def test_same_config_zero_delta(self):
        self.assertEqual(self.same["delta"]["total_damage"], 0.0)
        self.assertEqual(self.same["delta"]["damage_pct"], 0.0)

    def test_different_config_positive_delta(self):
        d = self.diff["delta"]
        self.assertGreater(d["total_damage"], 0)
        a = self.diff["sides"]["a"]["rotation"]["result"]["total_damage"]
        b = self.diff["sides"]["b"]["rotation"]["result"]["total_damage"]
        self.assertAlmostEqual(d["damage_pct"],
                               round((b - a) / a * 100.0, 2), places=1)

    def test_compare_is_deterministic(self):
        again = server.compare({
            "team": TEAM, "duration": 18,
            "a": {"mode": "cold", "enemy": "normal", "equip": "none", "weapon": 0},
            "b": {"mode": "cold", "enemy": "normal", "equip": "output", "weapon": 1},
        })
        self.assertEqual(again["delta"], self.diff["delta"])


class TestStatus(unittest.TestCase):
    def test_status_shape(self):
        st = server.status()
        for key in ("state", "percent", "message", "ready", "schema_version"):
            self.assertIn(key, st)
        self.assertIn(st["state"], ("idle", "building", "ready", "error"))

    def test_ready_reflects_state(self):
        old = dict(server.BUILD)
        try:
            server.BUILD.update(state="building", percent=42)
            st = server.status()
            self.assertFalse(st["ready"])
            self.assertEqual(st["percent"], 42)
            server.BUILD.update(state="ready", percent=100)
            self.assertTrue(server.status()["ready"])
        finally:
            server.BUILD.clear()
            server.BUILD.update(old)


class TestEnemyProfile(unittest.TestCase):
    def _attacks(self, profile: str, duration: float = 40.0) -> int:
        b = Battle(_ds(), Scenario(team=TEAM, duration_s=duration,
                                   enemy_profile=profile))
        b.advance_to(b.end)
        return len([e for e in b.log if e.get("kind") == "enemy_attack"])

    def test_slow_interval_applies_every_attack(self):
        # slow = 12s；40s 窗口应在 12/24/36s 各一次（不是只有第一次 12s）
        self.assertEqual(self._attacks("slow"), 3)

    def test_fast_more_attacks_than_slow(self):
        self.assertGreater(self._attacks("fast"), self._attacks("slow"))

    def test_normal_default_matches_8s(self):
        # 8s 间隔：40s 窗口内 8/16/24/32s 共 4 次（窗口末的 40s 不计）
        self.assertEqual(self._attacks("normal"), 4)

    def test_enemy_attacks_are_not_skipped(self):
        """跨事件推进时也不能丢敌人出手（此前会被静默跳过）。"""
        self.assertGreaterEqual(self._attacks("burst"), self._attacks("fast"))
        self.assertEqual(self._attacks("burst"), 9)
