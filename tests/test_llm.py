"""LLM 自更新层测试（提示词 / 校验 / 合并 / 配置 / 接口）。

全部用假 transport，不联网、不花钱。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                                   # noqa: E402
from endfield.model import Operator, Skill                    # noqa: E402
from endfield.semantics import apply as sem_apply             # noqa: E402
from endfield.semantics import llm, llm_config                # noqa: E402
from endfield.semantics.schema import validate_semantics       # noqa: E402

SKILL_TEXT = "主控干员受到攻击后可以发动。造成物理伤害并击飞。"


def valid_sem(**over) -> dict:
    sem = {
        "slot": "combo_skill",
        "trigger": "on_hit_taken",
        "trigger_params": {"hp_below_pct": None, "requires_status": None, "window_s": None},
        "element": "物理",
        "abnormal": "击飞",
        "target": "enemy",
        "role": "damage",
        "duration_s": None,
        "is_dot": False,
        "needs_enemy_hit": True,
        "gives_sp": None,
        "gives_ult": None,
        "heals": False,
        "shields": False,
        "tags": ["受击触发"],
        "evidence": ["主控干员受到攻击后可以发动", "造成物理伤害并击飞"],
        "confidence": 0.9,
        "method": "llm",
        "notes": [],
    }
    sem.update(over)
    return sem


class FakeTransport:
    """按顺序返回预置内容，并记录调用次数。"""

    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = 0
        self.payloads = []

    def __call__(self, payload, cfg):
        self.calls += 1
        self.payloads.append(payload)
        content = self.contents.pop(0) if len(self.contents) > 1 else self.contents[0]
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


class LLMTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ef-llm-"))
        cls._old_env = {k: os.environ.get(k) for k in
                        ("ENDFIELD_LLM_CONFIG", "ENDFIELD_LLM_API_KEY",
                         "ENDFIELD_LLM_BASE", "ENDFIELD_LLM_MODEL",
                         "ENDFIELD_LLM_ENABLED", "OPENAI_API_KEY")}
        for k in ("ENDFIELD_LLM_API_KEY", "ENDFIELD_LLM_BASE",
                  "ENDFIELD_LLM_MODEL", "ENDFIELD_LLM_ENABLED", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        os.environ["ENDFIELD_LLM_CONFIG"] = str(cls.tmp / "llm.json")
        cls._old_cache = config.LLM_CACHE
        config.LLM_CACHE = cls.tmp / "cache"
        config.LLM_CACHE.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        config.LLM_CACHE = cls._old_cache
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        llm.set_transport(None)
        (self.tmp / "llm.json").unlink(missing_ok=True)
        for f in config.LLM_CACHE.glob("sem_*.json"):
            f.unlink()
        llm_config.save({"enabled": True, "base": "https://api.example.com/v1",
                         "model": "test-model", "key": "test-key-1234567890"})
        self._reports = sem_apply.REPORTS
        sem_apply.REPORTS = self.tmp / "reports"
        sem_apply.REPORTS.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        sem_apply.REPORTS = self._reports
        llm.set_transport(None)


class TestConfig(LLMTestBase):
    def test_roundtrip_and_mask(self):
        cfg = llm_config.load()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["model"], "test-model")
        pub = llm_config.public(cfg)
        self.assertTrue(pub["has_key"])
        self.assertNotIn("test-key-1234567890", json.dumps(pub, ensure_ascii=False))
        self.assertIn("•••", pub["key_masked"])
        self.assertTrue(llm_config.available(cfg))

    def test_env_overrides_file(self):
        os.environ["ENDFIELD_LLM_BASE"] = "https://env.example.com/v1"
        try:
            self.assertEqual(llm_config.load()["base"], "https://env.example.com/v1")
        finally:
            os.environ.pop("ENDFIELD_LLM_BASE", None)

    def test_invalid_base_rejected(self):
        with self.assertRaises(ValueError):
            llm_config.save({"base": "ftp://nope"})

    def test_empty_key_keeps_existing(self):
        before = llm_config.load()["key"]
        llm_config.save({"key": ""})
        self.assertEqual(llm_config.load()["key"], before)


class TestPrompts(LLMTestBase):
    def test_system_prompt_has_schema_and_examples(self):
        text = llm.system_prompt()
        for token in ("trigger", "on_hit_taken", "evidence", "confidence",
                      "不要", "示例 1"):
            self.assertIn(token, text)
        self.assertGreater(len(text), 2000)

    def test_clean_text_strips_wiki_markup(self):
        self.assertEqual(llm.clean_text("造成{{术语|击飞}}并施加[[破防]]"),
                         "造成击飞并施加破防")

    def test_prompt_version_bumped(self):
        self.assertTrue(llm.PROMPT_VERSION.startswith("sem-"))


class TestAnnotate(LLMTestBase):
    def _skill(self):
        sk = Skill(skill_id="chr_test_combo_skill", slot="combo_skill",
                   name_zh="测试连携")
        sk.semantics = {"text_zh": SKILL_TEXT}
        return sk.to_dict()

    def test_success_and_cache(self):
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        usage = {}
        sem = llm.annotate_skill(self._skill(), usage_out=usage)
        self.assertIsNotNone(sem)
        self.assertEqual(sem["trigger"], "on_hit_taken")
        self.assertEqual(usage["calls"], 1)
        # 第二次：命中缓存，不再调用
        usage2 = {}
        sem2 = llm.annotate_skill(self._skill(), usage_out=usage2)
        self.assertEqual(sem2["trigger"], "on_hit_taken")
        self.assertEqual(fake.calls, 1)
        self.assertEqual(usage2.get("cache"), 1)

    def test_missing_evidence_rejected(self):
        bad = valid_sem(evidence=["这句话不在原文里"])
        fake = FakeTransport(json.dumps(bad, ensure_ascii=False))
        llm.set_transport(fake)
        self.assertIsNone(llm.annotate_skill(self._skill(), allow_repair=False))
        self.assertEqual(fake.calls, 1)

    def test_repair_then_success(self):
        bad = json.dumps(valid_sem(trigger="bogus_trigger"), ensure_ascii=False)
        good = json.dumps(valid_sem(), ensure_ascii=False)
        fake = FakeTransport(bad, good)
        llm.set_transport(fake)
        usage = {}
        sem = llm.annotate_skill(self._skill(), usage_out=usage)
        self.assertIsNotNone(sem)
        self.assertEqual(fake.calls, 2)
        self.assertEqual(usage.get("repaired"), 1)

    def test_numbers_are_stripped(self):
        sem = valid_sem(gives_sp=50, gives_ult=30)
        llm.set_transport(FakeTransport(json.dumps(sem, ensure_ascii=False)))
        out = llm.annotate_skill(self._skill())
        self.assertIsNone(out["gives_sp"])
        self.assertIsNone(out["gives_ult"])

    def test_no_text_no_call(self):
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        sk = Skill(skill_id="x", slot="battle_skill", name_zh="无描述")
        sk.semantics = {}
        self.assertIsNone(llm.annotate_skill(sk.to_dict()))
        self.assertEqual(fake.calls, 0)

    def test_unavailable_returns_none(self):
        llm_config.save({"enabled": False})
        self.assertIsNone(llm.annotate_skill(self._skill()))


class TestMerge(unittest.TestCase):
    def test_unknown_trigger_taken(self):
        rule = {"trigger": "unknown", "element": None, "abnormal": None,
                "confidence": 0.2, "tags": [], "needs_enemy_hit": False,
                "heals": False, "shields": False, "is_dot": False,
                "duration_s": None, "method": "rule", "notes": []}
        merged, changes, conflicts = sem_apply.merge(rule, valid_sem())
        self.assertEqual(merged["trigger"], "on_hit_taken")
        self.assertTrue(any(c["field"] == "trigger" for c in changes))
        self.assertFalse(conflicts)

    def test_rule_wins_on_conflict(self):
        rule = {"trigger": "none", "element": "物理", "abnormal": None,
                "confidence": 0.85, "tags": [], "needs_enemy_hit": False,
                "heals": False, "shields": False, "is_dot": False,
                "duration_s": None, "method": "rule", "notes": []}
        merged, changes, conflicts = sem_apply.merge(rule, valid_sem(element="寒冷"))
        self.assertEqual(merged["element"], "物理")
        self.assertTrue(conflicts)

    def test_booleans_or(self):
        rule = {"trigger": "none", "element": "物理", "abnormal": None,
                "confidence": 0.9, "tags": [], "needs_enemy_hit": False,
                "heals": False, "shields": False, "is_dot": False,
                "duration_s": None, "method": "rule", "notes": []}
        merged, _, _ = sem_apply.merge(rule, valid_sem(needs_enemy_hit=True))
        self.assertTrue(merged["needs_enemy_hit"])


class TestApplyPipeline(LLMTestBase):
    def _ops(self):
        op = Operator(char_id="chr_test", name_zh="测试干员", element="物理")
        sk = Skill(skill_id="chr_test_combo_skill", slot="combo_skill", name_zh="连携")
        sk.semantics = {"text_zh": SKILL_TEXT}
        op.skills = {"chr_test_combo_skill": sk}
        sk2 = Skill(skill_id="chr_test_battle_skill", slot="battle_skill", name_zh="战技")
        sk2.semantics = {"text_zh": "造成物理伤害。"}
        op.skills["chr_test_battle_skill"] = sk2
        return {"chr_test": op}

    def test_gaps_mode_calls_llm_and_reports(self):
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        ops = self._ops()
        stats = sem_apply.apply_to_operators(
            ops, use_llm=True, mode="all", limit=5, verbose=False)
        self.assertTrue(stats["llm_enabled"])
        self.assertGreaterEqual(stats["llm_calls"], 1)
        self.assertGreaterEqual(stats["llm_used"], 1)
        review = sem_apply.write_llm_review(ops, stats, llm_config.load())
        text = review.read_text(encoding="utf-8")
        self.assertIn("LLM 语义复核报告", text)
        self.assertIn("test-model", text)
        self.assertIn("测试干员", text)
        # 证据只引用原文
        for row in stats["changes"]:
            for ev in row["evidence"]:
                self.assertIn(ev, SKILL_TEXT)

    def test_gaps_mode_only_asks_uncertain(self):
        """规则已经确定的技能不花 token。"""
        op = Operator(char_id="chr_g", name_zh="已确定", element="物理")
        sk = Skill(skill_id="g1", slot="combo_skill", name_zh="连携")
        sk.semantics = {"text_zh": SKILL_TEXT}          # 规则可识别（受击）
        op.skills = {"g1": sk}
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        stats = sem_apply.apply_to_operators({"chr_g": op}, use_llm=True,
                                             mode="gaps", verbose=False)
        self.assertEqual(fake.calls, 0)
        self.assertEqual(stats["llm_used"], 0)

    def test_limit_respected(self):
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        stats = sem_apply.apply_to_operators(
            self._ops(), use_llm=True, mode="all", limit=1, verbose=False)
        self.assertEqual(stats["llm_calls"], 1)

    def test_disabled_no_calls(self):
        llm_config.save({"enabled": False})
        fake = FakeTransport(json.dumps(valid_sem(), ensure_ascii=False))
        llm.set_transport(fake)
        stats = sem_apply.apply_to_operators(self._ops(), use_llm=True,
                                             verbose=False)
        self.assertFalse(stats["llm_enabled"])
        self.assertEqual(fake.calls, 0)


class TestMechanics(LLMTestBase):
    def test_classify(self):
        content = json.dumps({"items": [
            {"key": "crit_up2", "category": "crit", "modeled_by_engine": False,
             "meaning": "暴击率提升", "confidence": 0.8},
            {"key": "bad_key", "category": "nonsense",
             "modeled_by_engine": False, "meaning": "", "confidence": 0.1},
        ]}, ensure_ascii=False)
        llm.set_transport(FakeTransport(content))
        rows = llm.classify_mechanics([{"key": "crit_up2", "samples": [0.1],
                                        "context": "套装"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["category"], "crit")
        self.assertEqual(rows[1]["category"], "other")   # 非法类别被收敛到 other


class TestServerLLM(LLMTestBase):
    def test_status_masks_key(self):
        from endfield.web import server
        st = server.llm_status()
        self.assertTrue(st["available"])
        self.assertNotIn("test-key-1234567890",
                         json.dumps(st, ensure_ascii=False))
        self.assertIn("prompt_version", st)

    def test_save_validates(self):
        from endfield.web import server
        with self.assertRaises(ValueError):
            server.llm_save({"base": "nope"})
        st = server.llm_save({"model": "m2"})
        self.assertTrue(st["saved"])
        self.assertEqual(llm_config.load()["model"], "m2")

    def test_test_endpoint(self):
        from endfield.web import server
        llm.set_transport(FakeTransport('{"ok": true}'))
        res = server.llm_test()
        self.assertTrue(res["ok"])
        self.assertEqual(res["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
