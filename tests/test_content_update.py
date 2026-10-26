"""数据质量 / 自更新能力测试（v2.4.0）。

这些测试守的是"数据变了以后还对不对"：
  * 同名分版只显示一个（管理员 男/女/占位）
  * 人工修正生效（噗切娜战技 = 自然）
  * 头像接口可用且路径安全
  * 新属性 id 按 iconName 归类；未知 id 不进通用增伤
  * 数据自检报告能生成
  * 新增干员（模拟未来版本）能被推荐流程接受
"""

from __future__ import annotations

import copy
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                          # noqa: E402
from endfield.engine.battle import Battle, Scenario  # noqa: E402
from endfield.model import Dataset, Operator         # noqa: E402
from endfield.web import server                      # noqa: E402


def _ds() -> Dataset:
    return Dataset.load(config.NORM)


class TestMetaDedup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from endfield.web import state as web_state
        web_state.STATE.mark_ready("test")
        cls.meta = server.meta()

    def test_no_duplicate_names(self):
        names = [o["name"] for o in self.meta["operators"]]
        self.assertEqual(len(names), len(set(names)), "同名干员应合并成一条")

    def test_admin_merged_with_variants(self):
        admin = [o for o in self.meta["operators"] if o["name"] == "管理员"]
        self.assertEqual(len(admin), 1)
        self.assertGreaterEqual(len(admin[0]["variants"]), 2,
                                "男/女/占位应记在 variants")
        self.assertNotIn("chr_9000_endmin", admin[0]["variants"])

    def test_entries_have_visual_fields(self):
        for o in self.meta["operators"][:10]:
            self.assertIn("avatar", o)
            self.assertTrue(o.get("eng_name"))


class TestCuratedData(unittest.TestCase):
    def test_purrche_nature_battle_skill(self):
        ds = _ds()
        op = ds.operators["chr_0038_purrche"]
        # 属性仍是物理（客户端 + Talos 一致），但战技是自然伤害/附着
        self.assertEqual(op.element, "物理")
        elems = [s.element for s in op.skills.values() if s.slot == "battle_skill"]
        self.assertEqual(elems, ["自然"])

    def test_curated_applied_recorded(self):
        import json
        m = json.loads((config.NORM / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("chr_0038_purrche", (m.get("curated") or {}))


class TestAvatarRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _ds()
        cls.cid = next(iter(cls.ds.operators))
        cls.has_avatar = (config.AVATARS / f"{cls.cid}.png").is_file()
        from endfield.web import state as web_state
        web_state.STATE.mark_ready("test")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        return urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=10)

    def test_avatar_or_404(self):
        try:
            r = self._get(f"/avatars/{self.cid}.png")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)      # 没下载到头像时允许 404（UI 有占位）
            return
        self.assertEqual(r.status, 200)
        self.assertTrue(r.read(8).startswith(b"\x89PNG"))

    def test_missing_avatar_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/avatars/chr_does_not_exist.png")
        self.assertEqual(ctx.exception.code, 404)

    def test_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/avatars/..%2F..%2Fdataset.json")
        self.assertEqual(ctx.exception.code, 404)

    def test_page_and_meta(self):
        with self._get("/") as r:
            body = r.read()
        self.assertIn(b"<html", body)
        with self._get("/api/meta") as r:
            self.assertEqual(r.status, 200)


class TestAttrCategorisation(unittest.TestCase):
    """新属性 id 应该按 iconName 归类，而不是一律当通用增伤。"""

    def setUp(self):
        self.ds = _ds()
        self.cid = next(iter(self.ds.operators))
        self.b = Battle(self.ds, Scenario(team=[self.cid], duration_s=1))

    def test_known_id(self):
        self.assertEqual(self.b._attr_category(50), ("element", "物理"))
        self.assertEqual(self.b._attr_category(32), ("slots", ("battle_skill",)))
        self.assertEqual(self.b._attr_category(29), ("heal", None))

    def test_new_id_by_icon(self):
        self.ds.constants.setdefault("attr_meta", {})["999"] = {
            "icon": "icon_fire_damage_increase", "name": "灼热伤害加成"}
        b = Battle(self.ds, Scenario(team=[self.cid], duration_s=1))
        self.assertEqual(b._attr_category(999), ("element", "灼热"))

    def test_unknown_id_goes_to_ignored(self):
        b = Battle(self.ds, Scenario(team=[self.cid], duration_s=1))
        m = b.mod[self.cid]
        b._add_mod_attr(m, 12345, 0.5, "T")
        self.assertIn(12345, m["ignored_attrs"])
        self.assertEqual(m["dmg_bonus"], 0.0)


class TestNewOperatorPipeline(unittest.TestCase):
    """模拟未来版本："表里出现一个新干员" 时推荐流程不应崩。"""

    def test_new_operator_can_be_recommended(self):
        from endfield.search.teams import TeamQuery, recommend

        ds = _ds()
        base = ds.operators["chr_0005_chen"]
        new = copy.deepcopy(base)
        new.char_id = "chr_9999_future"
        new.name_zh = "未来干员"
        new.eng_name = "Future Op"
        new.skills = {k.replace(base.char_id, new.char_id): sk
                      for k, sk in copy.deepcopy(base.skills).items()}
        ds.operators[new.char_id] = new

        res = recommend(ds, TeamQuery(must=["未来干员"], top_k=1, evaluate=1,
                                      duration_s=15), verbose=False)
        self.assertEqual(res["missing_names"], [])
        self.assertTrue(res["top"])
        self.assertIn(new.char_id, res["top"][0]["team"])

    def test_operator_without_wiki_text_still_works(self):
        ds = _ds()
        op = copy.deepcopy(ds.operators["chr_0005_chen"])
        op.char_id = "chr_9998_nowiki"
        op.name_zh = "无文本干员"
        op.wiki = {}
        op.skills = {}
        ds.operators[op.char_id] = op
        # 没技能不该让 meta / 推荐炸掉（会被 meta 过滤掉）
        from endfield.web import state as web_state
        prev = dict(web_state.STATE._cache)
        web_state.STATE._cache.update(ds=ds, ts=__import__("time").time())
        try:
            m = server.meta()
            self.assertNotIn("无文本干员", [o["name"] for o in m["operators"]])
        finally:
            web_state.STATE._cache.clear()
            web_state.STATE._cache.update(prev)


class TestQualityReport(unittest.TestCase):
    def test_report_exists_and_has_sections(self):
        from endfield.data import quality
        p = quality.write_report(_ds())
        text = p.read_text(encoding="utf-8")
        for key in ("覆盖概况", "需要人看一眼", "数据更新后还能不能算"):
            self.assertIn(key, text)
        self.assertIn("未定性", text)


if __name__ == "__main__":
    unittest.main()
