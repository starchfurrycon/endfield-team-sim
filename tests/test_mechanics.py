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
        dmg, ev, _ = s.apply_element("灼热", atk=1000, frame=0, level=80)
        self.assertEqual(dmg, 0.0)
        self.assertEqual(ev, "附着")
        self.assertEqual(s.arts_stacks, 1)

    def test_same_element_bursts(self):
        s = rx.ReactionState()
        s.apply_element("寒冷", 1000, 0, 80)
        dmg, ev, _ = s.apply_element("寒冷", 1000, 10, 80)
        self.assertEqual(ev, "爆发")
        self.assertGreater(dmg, 0)

    def test_different_element_reaction_scales_with_stacks(self):
        a = rx.ReactionState()
        a.apply_element("灼热", 1000, 0, 80)
        d1, e1, _ = a.apply_element("电磁", 1000, 10, 80)

        b = rx.ReactionState()
        b.apply_element("灼热", 1000, 0, 80)
        b.apply_element("灼热", 1000, 5, 80)
        b.apply_element("灼热", 1000, 6, 80)
        d2, e2, _ = b.apply_element("电磁", 1000, 10, 80)
        self.assertEqual(e1, "导电")   # 电磁触发 → 导电
        self.assertEqual(e2, "导电")
        self.assertGreater(d2, d1, "附着层数越多，异常伤害越高")

    def test_physical_is_element_agnostic(self):
        s = rx.ReactionState()
        self.assertEqual(s.apply_element("物理", 1000, 0, 80), (0.0, None, []))


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
        s1 = rx.ReactionState(vuln=3, vuln_until=10**9)
        d_crush, _ = s1.apply_physical("猛击", 1000, 0, 80)
        s2 = rx.ReactionState(vuln=3, vuln_until=10**9)
        d_breach, _ = s2.apply_physical("碎甲", 1000, 0, 80)
        self.assertGreater(d_crush, d_breach)

    def test_vulnerable_capped(self):
        s = rx.ReactionState(vuln=rx.MAX_VULN, vuln_until=10**9)
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


class TestTalentPotential(unittest.TestCase):
    """天赋/潜能：来自 PotentialTalentEffectTable 的无条件数值修正。"""

    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()
        cls.op = cls.ds.resolve("陈千语")

    def test_mods_parsed(self):
        mods = self.op.mods or {}
        self.assertIn("potentials", mods)
        self.assertIn("2", mods["potentials"])

    def test_potential_raises_atk(self):
        cid = self.op.char_id
        b0 = Battle(self.ds, Scenario(team=[cid], potential_level=0))
        b5 = Battle(self.ds, Scenario(team=[cid], potential_level=5))
        self.assertGreater(b5._atk(cid), b0._atk(cid))

    def test_potential_adds_dmg_bonus(self):
        """潜能 2 的「物理伤害 +8%」按属性口径进**元素**乘区，不是通用增伤。

        属性 id 50 = 物理伤害加成（AttributeMetaTable.iconName =
        icon_physical_damage_increase）。此前它被当成通用 dmg_bonus，
        会让装备/潜能对全部伤害生效，属于过量建模。
        """
        cid = self.op.char_id
        b0 = Battle(self.ds, Scenario(team=[cid], potential_level=0))
        b5 = Battle(self.ds, Scenario(team=[cid], potential_level=5))
        self.assertGreater(b5.mod[cid]["dmg_element"].get("物理", 0.0),
                           b0.mod[cid]["dmg_element"].get("物理", 0.0))
        self.assertEqual(b5.mod[cid]["dmg_bonus"], b0.mod[cid]["dmg_bonus"])

    def test_conditional_entries_skipped(self):
        for cid, m in Battle(self.ds, Scenario(
                team=[o.char_id for o in list(self.ds.operators.values())[:4]])).mod.items():
            for tag in m["applied"]:
                self.assertIsInstance(tag, str)


class TestBlackboardMods(unittest.TestCase):
    """潜能对技能倍率的修正（skillBbModifier），按 skillId + 黑板 key 精确作用。"""

    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()
        cls.op = cls.ds.resolve("陈千语")

    def test_scale_applies_to_target_skill_only(self):
        cid = self.op.char_id
        b = Battle(self.ds, Scenario(team=[cid], potential_level=5))
        for sk in self.op.skills.values():
            if sk.slot not in ("battle_skill", "combo_skill", "ultimate"):
                continue
            base = sk.total_multiplier(b.sc.skill_level)
            scaled = b._scaled_mult(cid, sk)
            if base <= 0:
                continue
            self.assertAlmostEqual(scaled / base, 1.1, places=3,
                                   msg=f"{sk.slot} 应为 +10%")

    def test_no_potential_means_no_scale(self):
        cid = self.op.char_id
        b0 = Battle(self.ds, Scenario(team=[cid], potential_level=0))
        sk = next(s for s in self.op.skills.values() if s.slot == "battle_skill")
        self.assertAlmostEqual(b0._scaled_mult(cid, sk),
                               sk.total_multiplier(b0.sc.skill_level), places=4)


class TestEquipment(unittest.TestCase):
    """装备：EquipTable 的属性向量按精锻等级累加。"""

    @classmethod
    def setUpClass(cls):
        import json as _json
        cls.ds = _ds()
        p = config.NORM / "equip.json"
        cls.equip = _json.loads(p.read_text(encoding="utf-8"))
        cls.op = cls.ds.resolve("陈千语")
        cls.piece = next(iter(cls.equip["items"]))

    def test_index_present(self):
        self.assertGreater(len(self.equip["items"]), 100)
        self.assertGreater(len(self.equip["suits"]), 10)

    def test_equip_raises_atk(self):
        cid = self.op.char_id
        b0 = Battle(self.ds, Scenario(team=[cid]))
        b1 = Battle(self.ds, Scenario(team=[cid], equip_ids=[self.piece]))
        self.assertGreaterEqual(b1._atk(cid), b0._atk(cid))

    def test_equip_level_scales(self):
        cid = self.op.char_id
        b_low = Battle(self.ds, Scenario(team=[cid], equip_ids=[self.piece],
                                         equip_level=0))
        b_high = Battle(self.ds, Scenario(team=[cid], equip_ids=[self.piece],
                                          equip_level=3))
        self.assertGreaterEqual(b_high._atk(cid), b_low._atk(cid))

    def test_default_has_no_equipment(self):
        cid = self.op.char_id
        b = Battle(self.ds, Scenario(team=[cid]))
        # 默认不穿装备：不应出现 EQ: 前缀的条目（武器被动 W: 是另一回事）
        eq = [a for a in b.mod[cid]["applied"] if str(a).startswith("EQ:")]
        self.assertEqual(eq, [])


class TestLoadout(unittest.TestCase):
    def test_weapon_recommendations(self):
        from endfield.search import loadout
        ds = _ds()
        cid = ds.resolve("陈千语").char_id
        recs = loadout.recommend_weapons(ds, cid, top=3)
        self.assertEqual(len(recs), 3)
        scores = [r["score"] for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_equipment_is_complete_set(self):
        from endfield.search import loadout
        ds = _ds()
        cid = ds.resolve("陈千语").char_id
        sets = loadout.recommend_equipment(ds, cid, top=1)
        self.assertEqual(len(sets), 1)
        for s in sets:
            self.assertEqual(len(s["pieces"]), 4)
            self.assertGreaterEqual(s["set_count"], s["need"])
            body = sum(1 for x in s["pieces"] if "_body_" in x)
            hand = sum(1 for x in s["pieces"] if "_hand_" in x)
            edc = sum(1 for x in s["pieces"] if "_edc_" in x)
            self.assertEqual((body, hand, edc), (1, 1, 2))

    def test_signature_weapon_first(self):
        from endfield.search import loadout
        ds = _ds()
        op = ds.resolve("伊冯")
        rows = loadout.recommend_weapons(ds, op.char_id, top=3)
        self.assertEqual(rows[0]["name"], "艺术暴君")
        self.assertEqual(rows[0]["tier"], "专武")
        self.assertEqual(rows[1]["tier"], "大月卡")


if __name__ == "__main__":
    unittest.main()

class TestArtsStatuses(unittest.TestCase):
    """状态等级 I~IV、燃烧 DoT、腐蚀减抗、导电增伤、附着实效。"""

    def test_status_level_equals_consumed_stacks(self):
        for stacks in (1, 2, 3, 4):
            s = rx.ReactionState()
            for i in range(stacks):
                s.apply_element("灼热", 1000, i, 80)
            dmg, kind, fu = s.apply_element("电磁", 1000, 100, 80)
            self.assertEqual(kind, "导电")
            self.assertEqual(s.statuses["导电"]["level"], stacks)
            self.assertEqual(len(fu), 0)      # 导电没有 DoT

    def test_combustion_dot_ticks(self):
        s = rx.ReactionState()
        s.apply_element("寒冷", 1000, 0, 80)      # 先铺寒冷
        for i in range(1, 4):
            s.apply_element("寒冷", 1000, i, 80)
        dmg, kind, fu = s.apply_element("灼热", 1000, 100, 80)   # 灼热 → 燃烧
        self.assertEqual(kind, "燃烧")
        self.assertEqual(len(fu), 10, "燃烧 10 秒每秒一跳")
        self.assertTrue(all(d > 0 for _, d, _ in fu))
        self.assertTrue(all(lb == "燃烧" for _, _, lb in fu))

    def test_corrosion_res_shred_ramps_and_caps(self):
        s = rx.ReactionState()
        s.apply_element("自然", 1000, 0, 80)
        for i in range(1, 4):
            s.apply_element("自然", 1000, i, 80)
        s.apply_element("灼热", 1000, 100, 80)   # 灼热 → 燃烧而不是腐蚀
        s2 = rx.ReactionState()
        s2.apply_element("灼热", 1000, 0, 80)
        for i in range(1, 4):
            s2.apply_element("灼热", 1000, i, 80)
        dmg, kind, _ = s2.apply_element("自然", 1000, 100, 80)
        self.assertEqual(kind, "腐蚀")
        early = s2.res_shred(100)
        later = s2.res_shred(100 + 10 * 30)
        self.assertGreater(later, early)
        self.assertLessEqual(later, 12.0 * 4)

    def test_electrification_boost_expires(self):
        s = rx.ReactionState()
        s.apply_element("灼热", 1000, 0, 80)
        for i in range(1, 4):
            s.apply_element("灼热", 1000, i, 80)
        s.apply_element("电磁", 1000, 100, 80)
        self.assertGreater(s.arts_dmg_taken(110), 1.0)
        self.assertEqual(s.arts_dmg_taken(100 + rx.ELECTRIFICATION_FRAMES + 10), 1.0)

    def test_infliction_expires_after_20s(self):
        s = rx.ReactionState()
        s.apply_element("寒冷", 1000, 0, 80)
        self.assertEqual(s.arts_stacks, 1)
        # 超过 20 秒后再次附着，应重新从 1 层开始
        dmg, kind, _ = s.apply_element("寒冷", 1000, rx.INFLICTION_FRAMES + 1, 80)
        self.assertEqual(kind, "附着")
        self.assertEqual(s.arts_stacks, 1)

    def test_arts_intensity_scales_damage(self):
        a = rx.ReactionState()
        a.apply_element("灼热", 1000, 0, 80, ai=0)
        d0, _, _ = a.apply_element("电磁", 1000, 100, 80, ai=0)
        b = rx.ReactionState()
        b.apply_element("灼热", 1000, 0, 80, ai=100)
        d1, _, _ = b.apply_element("电磁", 1000, 100, 80, ai=100)
        self.assertAlmostEqual(d1 / d0, 2.0, places=2)

    def test_shatter_on_frozen(self):
        s = rx.ReactionState()
        s.apply_element("自然", 1000, 0, 80)
        s.apply_element("寒冷", 1000, 10, 80)     # 冻结
        self.assertTrue(s.is_frozen(20))
        dmg, ev = s.consume_frozen(20, 1000, 80)
        self.assertGreater(dmg, 0)
        self.assertEqual(ev, "碎冰")
        self.assertFalse(s.is_frozen(20))
