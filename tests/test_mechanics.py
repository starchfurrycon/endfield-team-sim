"""机制级测试：元素/物理异常、武器、自动普攻与引擎不变量。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                          # noqa: E402
from endfield.model import Dataset                   # noqa: E402
from endfield.engine import reactions as rx          # noqa: E402
from endfield.engine.battle import Battle, Scenario  # noqa: E402


def _ds() -> Dataset:
    return Dataset.load(config.NORM)


class TestArtsReactions(unittest.TestCase):
    def test_first_hit_attaches_only(self):
        s = rx.ReactionState()
        dmg, ev = s.apply_element("灼热", atk=1000, frame=0, level=80)
        self.assertEqual(dmg, 0.0)
        self.assertEqual(ev, "附着")
        self.assertEqual(s.arts_stacks, 1)

    def test_same_element_bursts(self):
        s = rx.ReactionState()
        s.apply_element("寒冷", 1000, 0, 80)
        dmg, ev = s.apply_element("寒冷", 1000, 10, 80)
        self.assertEqual(ev, "爆发")
        self.assertGreater(dmg, 0)

    def test_different_element_reaction_scales_with_stacks(self):
        a = rx.ReactionState()
        a.apply_element("灼热", 1000, 0, 80)
        d1, e1 = a.apply_element("电磁", 1000, 10, 80)

        b = rx.ReactionState()
        b.apply_element("灼热", 1000, 0, 80)
        b.apply_element("灼热", 1000, 5, 80)
        b.apply_element("灼热", 1000, 6, 80)
        d2, e2 = b.apply_element("电磁", 1000, 10, 80)
        self.assertEqual(e1, "异常")
        self.assertEqual(e2, "异常")
        self.assertGreater(d2, d1, "附着层数越多，异常伤害越高")

    def test_physical_is_element_agnostic(self):
        s = rx.ReactionState()
        self.assertEqual(s.apply_element("物理", 1000, 0, 80), (0.0, None))


class TestPhysicalReactions(unittest.TestCase):
    def test_lift_applies_vulnerable_then_damages(self):
        s = rx.ReactionState()
        dmg1, ev1 = s.apply_physical("击飞", 1000, 0, 80)
        self.assertEqual(dmg1, 0.0)
        self.assertEqual(s.vuln, 1)
        dmg2, ev2 = s.apply_physical("击飞", 1000, 10, 80)
        self.assertGreater(dmg2, 0)
        self.assertEqual(s.vuln, 2)

    def test_crush_consumes_stacks(self):
        s = rx.ReactionState()
        s.apply_physical("击飞", 1000, 0, 80)
        s.apply_physical("倒地", 1000, 5, 80)
        self.assertEqual(s.vuln, 2)
        dmg, ev = s.apply_physical("猛击", 1000, 10, 80)
        self.assertEqual(ev, "猛击")
        self.assertEqual(s.vuln, 0, "猛击应消耗全部破防层数")
        self.assertGreater(dmg, 0)

    def test_breach_weaker_than_crush(self):
        s1 = rx.ReactionState(vuln=3)
        d_crush, _ = s1.apply_physical("猛击", 1000, 0, 80)
        s2 = rx.ReactionState(vuln=3)
        d_breach, _ = s2.apply_physical("碎甲", 1000, 0, 80)
        self.assertGreater(d_crush, d_breach)

    def test_vulnerable_capped(self):
        s = rx.ReactionState(vuln=rx.MAX_VULN)
        s.apply_physical("击飞", 1000, 0, 80)
        self.assertEqual(s.vuln, rx.MAX_VULN)


class TestWeapon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()

    def test_weapon_curve_present(self):
        op = self.ds.resolve("陈千语")
        curve = (op.weapon or {}).get("atk_by_level") or []
        self.assertGreater(len(curve), 50)
        self.assertGreater(curve[-1], curve[0])

    def test_weapon_raises_atk(self):
        op = self.ds.resolve("陈千语")
        ids = [op.char_id, op.char_id]
        low = Battle(self.ds, Scenario(team=[op.char_id], weapon_level=1))
        high = Battle(self.ds, Scenario(team=[op.char_id], weapon_level=90))
        self.assertGreater(high._atk(op.char_id), low._atk(op.char_id))

    def test_weapon_level_clamped(self):
        op = self.ds.resolve("陈千语")
        b = Battle(self.ds, Scenario(team=[op.char_id], weapon_level=999))
        self.assertGreater(b._atk(op.char_id), 0)


class TestSkillRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()

    def test_heal_and_buff_roles_exist(self):
        roles = {sk.role for op in self.ds.operators.values()
                 for sk in op.skills.values()}
        self.assertIn("damage", roles)
        self.assertIn("heal", roles)
        self.assertIn("buff", roles)

    def test_zero_multiplier_are_support_skills(self):
        """倍率为 0 的技能应当都是治疗/增益，不是数据缺陷。"""
        bad = []
        for op in self.ds.operators.values():
            for sk in op.skills.values():
                if sk.slot not in ("battle_skill", "ultimate", "combo_skill"):
                    continue
                if sk.total_multiplier(7) == 0 and sk.role == "damage":
                    bad.append(f"{op.name_zh}/{sk.slot}")
        self.assertEqual(bad, [], f"这些技能没倍率却标为 damage：{bad}")


class TestReactionIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()

    def test_skill_applies_infliction_then_burst(self):
        """元素技能才叠附着：连放两次战技 → 附着 + 爆发。"""
        from endfield.engine.battle import Action
        b = Battle(self.ds, Scenario(team=["chr_0021_whiten", "chr_0005_chen"],
                                     duration_s=20))
        for _ in range(2):
            acts = [a for a in b.legal_actions(include_wait=False)
                    if a.kind == "skill"]
            if not acts:
                break
            b.apply(acts[0])
        b.advance_to(b.end)
        rx_stats = b.result().stats.get("reactions") or {}
        counts = rx_stats.get("counts") or {}
        self.assertGreaterEqual(counts.get("附着", 0) + counts.get("爆发", 0), 1)

    def test_normal_attacks_do_not_inflict(self):
        """普攻/自动普攻不产生附着（附着由技能触发）。"""
        b = Battle(self.ds, Scenario(team=["chr_0005_chen", "chr_0021_whiten"],
                                     duration_s=20))
        b.advance_to(b.end)          # 主控不出手，只有自动普攻
        rx_stats = b.result().stats.get("reactions") or {}
        self.assertIsNone(rx_stats.get("arts_attached"))
        self.assertEqual(sum((rx_stats.get("totals") or {}).values()), 0)


if __name__ == "__main__":
    unittest.main()
