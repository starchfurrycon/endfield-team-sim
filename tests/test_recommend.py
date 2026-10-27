"""推荐流程：约束（必选/排除）→ Top N；同名干员去重。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                       # noqa: E402
from endfield.model import Dataset                # noqa: E402
from endfield.search.teams import TeamQuery, pool_of, recommend  # noqa: E402


class TestRecommend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.load(config.NORM)

    def test_pool_has_no_duplicate_names(self):
        pool, _ = pool_of(self.ds, TeamQuery())
        names = [o.name_zh or o.char_id for o in pool]
        self.assertEqual(len(names), len(set(names)))

    def test_no_constraints_returns_teams(self):
        r = recommend(self.ds, TeamQuery(top_k=3, evaluate=6, duration_s=20),
                      verbose=False)
        self.assertGreaterEqual(len(r["top"]), 1)
        for t in r["top"]:
            names = t["names"]
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(len(names), 4)

    def test_top_teams_are_diverse(self):
        r = recommend(self.ds, TeamQuery(top_k=3, evaluate=16, duration_s=20),
                      verbose=False)
        teams = [set(t["team"]) for t in r["top"]]
        for i in range(len(teams)):
            for j in range(i):
                self.assertLessEqual(len(teams[i] & teams[j]), 2)

    def test_must_and_ban(self):
        r = recommend(self.ds, TeamQuery(must=["洛茜"], ban=["狼卫"],
                                         top_k=3, evaluate=6, duration_s=20),
                      verbose=False)
        self.assertTrue(r["top"])
        for t in r["top"]:
            self.assertIn("洛茜", t["names"])
            self.assertNotIn("狼卫", t["names"])


if __name__ == "__main__":
    unittest.main()
