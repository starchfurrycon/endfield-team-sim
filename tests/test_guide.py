"""「怎么打」教学生成 + /api/axis 契约。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                      # noqa: E402
from endfield.engine.battle import Scenario      # noqa: E402
from endfield.model import Dataset               # noqa: E402
from endfield.report import guide                # noqa: E402
from endfield.search.rotations import search_rotation  # noqa: E402

TEAM_NAMES = ("陈千语", "大潘", "萤石", "埃特拉")


class TestGuide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.load(config.NORM)
        cls.ids = [o.char_id for o in cls.ds.operators.values()
                   if o.name_zh in TEAM_NAMES]
        cls.sc = Scenario(team=cls.ids, duration_s=30)
        cls.rr = search_rotation(cls.ds, cls.sc, beam=2, steps=14)
        cls.g = guide.build(cls.ds, cls.sc, cls.rr.result)

    def test_steps_are_skill_casts_in_order(self):
        steps = self.g["steps"]
        self.assertTrue(steps)
        self.assertLessEqual(len(steps), guide.MAX_STEPS)
        for s in steps:
            self.assertIn(s["label"], ("战技", "连携", "终结技"))
            self.assertIn("t", s)
            self.assertGreaterEqual(s["t"], 0.0)
        times = [s["t"] for s in steps]
        self.assertEqual(times, sorted(times))

    def test_merge_repeated_casts(self):
        for s in self.g["steps"]:
            self.assertGreaterEqual(s["count"], 1)
            self.assertLessEqual(s["t_end"], 30.5)

    def test_tips_are_short_and_present(self):
        tips = self.g["tips"]
        self.assertTrue(1 <= len(tips) <= 3)
        for t in tips:
            self.assertLess(len(t), 80)

    def test_ultimate_tip_uses_energy_caps(self):
        caps = (self.rr.result.stats or {}).get("energy_cap") or {}
        if not caps:
            self.skipTest("no energy caps")
        text = " ".join(self.g["tips"])
        self.assertIn("大招", text)

    def test_note_mentions_commit_frames(self):
        self.assertIn("承诺帧", self.g["note"])

    def test_axis_api_returns_guide(self):
        from endfield.web import api
        from endfield.web import state as web_state
        web_state.STATE.mark_ready("test")
        out = api.axis({"team": self.ids, "duration": 20,
                        "beam": 1, "steps": 8})
        self.assertIn("guide", out)
        self.assertIn("steps", out["guide"])
        self.assertIn("tips", out["guide"])


if __name__ == "__main__":
    unittest.main()
