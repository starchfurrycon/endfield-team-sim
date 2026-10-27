"""LLM 自更新能力审计：能标、能拒、能退化、能适配新文本。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield.model import Operator, Skill          # noqa: E402
from endfield.semantics import apply as sem_apply   # noqa: E402
from endfield.semantics import llm, schema, selftest  # noqa: E402

CFG = {"enabled": True, "base": "http://stub/v1", "model": "stub-model",
       "key": "test-key-123456", "temperature": 0, "max_tokens": 2048,
       "timeout_s": 10, "price_in": 0, "price_out": 0}

TEXT = "当敌人处于过载状态时，造成电磁伤害并附加感电。"


def sem(**kw) -> dict:
    base = {
        "slot": "battle_skill", "trigger": "on_status",
        "trigger_params": {"hp_below_pct": None, "requires_status": "过载",
                           "window_s": None},
        "element": "电磁", "abnormal": "导电", "target": "enemy",
        "role": "damage", "duration_s": None, "is_dot": False,
        "needs_enemy_hit": False, "gives_sp": 30, "gives_ult": 20,
        "heals": False, "shields": False, "tags": ["过载"],
        "evidence": ["造成电磁伤害"], "confidence": 0.9, "method": "llm",
        "notes": [],
    }
    base.update(kw)
    return base


class Fake:
    def __init__(self, payload: dict, fix_evidence: bool = True):
        self.payload = payload
        self.fix_evidence = fix_evidence
        self.calls = 0

    def __call__(self, body: dict, cfg: dict) -> dict:
        self.calls += 1
        text = ""
        for m in body.get("messages", []):
            if m.get("role") == "user":
                try:
                    text = json.loads(m.get("content") or "{}").get("text") or ""
                except Exception:  # noqa: BLE001
                    text = ""
        payload = dict(self.payload)
        if text and self.fix_evidence:
            payload["evidence"] = [text[:8]]
        return {
            "choices": [{"message": {"content": json.dumps(payload,
                                                           ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }


def _op() -> Operator:
    op = Operator(char_id="chr_future", name_zh="未来干员", element=None)
    sk = Skill(skill_id="sk_future", slot="combo_skill", name_zh="过载打击",
               element=None)
    sk.semantics = {"text_zh": TEXT}
    op.skills = {"sk_future": sk}
    return op


class TestSelfTest(unittest.TestCase):
    def test_offline_passes_with_skip(self):
        res = selftest.run(cfg={"enabled": False, "key": "", "model": "x"})
        self.assertTrue(res["ok"])
        names = {c["name"]: c["ok"] for c in res["checks"]}
        self.assertTrue(names["规则兜底"])
        self.assertTrue(names["提示词 semantics_system.md"])
        self.assertIsNone(names["LLM 标注"])

    def test_online_with_stub_passes(self):
        fake = Fake(sem(slot="combo_skill"))
        res = selftest.run(cfg=CFG, transport=fake)
        self.assertTrue(res["ok"])
        names = {c["name"]: c["ok"] for c in res["checks"]}
        self.assertTrue(names["LLM 标注"])
        self.assertTrue(names["证据校验"])
        self.assertTrue(names["不产数值"])


class TestAdaptation(unittest.TestCase):
    def test_new_text_gets_semantics(self):
        fake = Fake(sem(slot="combo_skill"))
        old = getattr(llm, "_TRANSPORT", None)
        llm.set_transport(fake)
        try:
            ops = {"chr_future": _op()}
            stats = sem_apply.apply_to_operators(
                ops, use_llm=True, mode="all", cfg=CFG, verbose=False)
        finally:
            llm.set_transport(old)
        sk = ops["chr_future"].skills["sk_future"]
        self.assertGreaterEqual(stats["llm_used"], 1)
        self.assertEqual(sk.semantics.get("element"), "电磁")
        self.assertEqual(sk.semantics.get("trigger"), "on_status")
        self.assertEqual(sk.semantics.get("method"), "merged")
        # 数值字段不允许被 LLM 写入
        self.assertIsNone(sk.semantics.get("gives_sp"))
        self.assertIsNone(sk.semantics.get("gives_ult"))

    def test_bad_evidence_is_rejected(self):
        payload = sem(evidence=["原文里没有的一句话"])
        fake = Fake(payload, fix_evidence=False)
        old = getattr(llm, "_TRANSPORT", None)
        llm.set_transport(fake)
        try:
            got = llm.annotate_skill(
                {"operator": "未来干员", "slot": "battle_skill",
                 "element": None, "semantics": {"text_zh": TEXT}},
                cfg=CFG, use_cache=False, allow_repair=False)
        finally:
            llm.set_transport(old)
        self.assertIsNone(got)

    def test_no_api_falls_back_to_rules(self):
        ops = {"chr_future": _op()}
        stats = sem_apply.apply_to_operators(
            ops, use_llm=False, mode="all", cfg={"enabled": False, "key": ""},
            verbose=False)
        self.assertFalse(stats["llm_enabled"])
        self.assertEqual(stats["llm_used"], 0)
        self.assertIsNotNone(ops["chr_future"].skills["sk_future"].semantics)

    def test_schema_rejects_invented_numbers(self):
        bad = sem(element="电磁")
        bad["gives_sp"] = 999
        # schema 本身允许字段存在；apply 会强制清空（见上）；这里验证枚举
        bad["element"] = "火焰"
        ok, errs = schema.validate_semantics(bad)
        self.assertFalse(ok)
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
