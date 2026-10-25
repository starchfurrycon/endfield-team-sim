"""核心逻辑单测（stdlib unittest，无需 pytest）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                      # noqa: E402
from endfield.model import Dataset, Timing       # noqa: E402
from endfield.engine import damage as dmg        # noqa: E402
from endfield.engine.battle import Action, Battle, Scenario  # noqa: E402
from endfield.semantics import rules, schema     # noqa: E402


def _dataset() -> Dataset:
    return Dataset.load(config.NORM)


class TestTiming(unittest.TestCase):
    def test_commit_prefers_exclusive(self):
        t = Timing(duration_frames=100, exclusive_frames=40)
        self.assertEqual(t.commit_frames, 40)

    def test_commit_falls_back_to_duration(self):
        self.assertEqual(Timing(duration_frames=100).commit_frames, 100)

    def test_hit_split_by_damage_type(self):
        t = Timing(hits=[])
        self.assertEqual(t.hit_frames, [])


class TestDamage(unittest.TestCase):
    def test_def_zone(self):
        d, zones = dmg.compute(dmg.DamageContext(atk=100, multiplier=1.0,
                                                enemy_def=100, crit_rate=0.0))
        self.assertAlmostEqual(zones["def"], 0.5)
        self.assertEqual(d, 50)

    def test_stagger_multiplier(self):
        _, zones = dmg.compute(dmg.DamageContext(atk=100, multiplier=1.0,
                                                staggered=True))
        self.assertAlmostEqual(zones["stagger"], config.guess("STAGGER_DMG_MULT"))

    def test_atk_scaling(self):
        a = dmg.effective_atk(100, 100, 0)
        b = dmg.effective_atk(100, 0, 0)
        self.assertGreater(a, b)

    def test_rounding_half_up(self):
        d, _ = dmg.compute(dmg.DamageContext(atk=101, multiplier=1.0,
                                            enemy_def=100, crit_rate=0.0))
        self.assertEqual(d, round(101 * 0.5))


class TestSemantics(unittest.TestCase):
    def test_trigger_detects_hit_taken(self):
        sem = rules.extract_skill({
            "slot": "combo_skill", "element": None,
            "semantics": {"text_zh": "当主控干员受到攻击后，如果生命值低于60%可以发动。"}})
        self.assertEqual(sem["trigger"], "on_hit_taken")
        self.assertTrue(sem["needs_enemy_hit"])

    def test_trigger_detects_break(self):
        sem = rules.extract_skill({
            "slot": "combo_skill",
            "semantics": {"text_zh": "当有敌人进入破防状态时可以发动。"}})
        self.assertIn(sem["trigger"], ("on_break", "on_status"))

    def test_battle_skill_is_unconditional(self):
        sem = rules.extract_skill({
            "slot": "battle_skill",
            "semantics": {"text_zh": "若在立盾期间受到攻击，则发动一次反击。"}})
        self.assertEqual(sem["trigger"], "none")
        self.assertTrue(sem["needs_enemy_hit"])

    def test_schema_validation(self):
        ok, errs = schema.validate_semantics({
            "slot": "battle_skill", "trigger": "none",
            "confidence": 0.9, "method": "rule"})
        self.assertTrue(ok, errs)
        bad, errs2 = schema.validate_semantics({
            "slot": "battle_skill", "trigger": "nonsense",
            "confidence": 2, "method": "rule"})
        self.assertFalse(bad)


class TestBattleInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _dataset()
        cls.team = [o.char_id for o in
                    sorted(cls.ds.operators.values(), key=lambda o: o.char_id)[:4]]

    def test_random_rotations_keep_invariants(self):
        rng = random.Random(7)
        for trial in range(20):
            sc = Scenario(team=self.team, duration_s=30,
                          start_energy="cold" if trial % 2 else "hot")
            b = Battle(self.ds, sc)
            for _ in range(80):
                if b.t >= b.end:
                    break
                acts = b.legal_actions(include_wait=False)
                if not acts:
                    nxt = b.next_event_frame()
                    if nxt <= b.t:
                        break
                    b.advance_to(nxt)
                    continue
                b.apply(rng.choice(acts))
                self.assertGreaterEqual(b.sp, -1e-6, "技力为负")
                self.assertLessEqual(b.sp, config.guess("SP_MAX") + 1e-6)
            res = b.result()
            self.assertGreaterEqual(res.total_damage, 0)

    def test_reproducible(self):
        sc = Scenario(team=self.team, duration_s=20)
        a = Battle(self.ds, sc)
        b = Battle(self.ds, sc)
        for _ in range(20):
            for x in (a, b):
                acts = x.legal_actions(include_wait=False)
                if acts:
                    x.apply(acts[0])
                else:
                    x.advance_to(x.next_event_frame())
        self.assertAlmostEqual(a.result().total_damage,
                               b.result().total_damage, places=3)

    def test_energy_never_exceeds_cap(self):
        b = Battle(self.ds, Scenario(team=self.team, start_energy="cold",
                                     duration_s=45))
        for _ in range(200):
            if b.t >= b.end:
                break
            acts = b.legal_actions(include_wait=False)
            if not acts:
                b.advance_to(b.next_event_frame())
                continue
            b.apply(acts[0])
            for cid, e in b.energy.items():
                self.assertLessEqual(e, b._energy_cap(cid) + 1e-6)


class TestDataset(unittest.TestCase):
    def test_all_operators_load(self):
        ds = _dataset()
        self.assertGreater(len(ds.operators), 20)
        for op in ds.operators.values():
            self.assertTrue(op.name_zh, op.char_id)
            # chr_9000_endmin 是「管理员」的占位条目，技能挂在男/女版本上
            if op.char_id == "chr_9000_endmin":
                continue
            self.assertTrue(op.skills, op.char_id)

    def test_key_operators_present(self):
        ds = _dataset()
        for name in ("陈千语", "佩丽卡", "昼雪"):
            self.assertIsNotNone(ds.resolve(name), name)

    def test_skill_timing_present(self):
        ds = _dataset()
        missing = []
        for op in ds.operators.values():
            for sk in op.skills.values():
                if sk.timing.duration_frames is None:
                    missing.append(sk.skill_id)
        self.assertLess(len(missing), 10, missing[:5])


if __name__ == "__main__":
    unittest.main()
