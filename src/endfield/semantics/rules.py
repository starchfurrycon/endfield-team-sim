"""规则抽取器（主通道）。

输入：技能中文描述（wiki 模板字段）
输出：符合 schema 的语义对象 + confidence

置信度含义：
  0.9 = 明确关键词命中，且句子结构无歧义
  0.6 = 命中了关键词，但目标/时长靠上下文推断
  0.3 = 只有兜底分类
"""

from __future__ import annotations

import re
from typing import Any

from .schema import validate_semantics

ELEMENT_WORDS = ("灼热", "电磁", "寒冷", "自然", "物理", "超域")

# 顺序敏感：先匹配更具体的
TRIGGER_RULES: list[tuple[str, str, float]] = [
    ("on_hit_taken", r"(受到(敌人)?攻击后|受击|被攻击后|格挡.{0,4}攻击后|在立盾期间受到攻击)", 0.9),
    ("on_dodge", r"(完美闪避|闪避成功后|闪避后)", 0.95),
    ("on_execute", r"处决", 0.8),
    ("on_break", r"(失衡状态|处于失衡|有敌人失衡|触发失衡节点|破防状态|进入破防|层破防)", 0.8),
    ("on_heavy_attack", r"重击", 0.85),
    ("on_status", r"(被施加|被消耗|次数耗尽|达到\s*\d+\s*层|法术附着|法术异常|"
                  r"源石结晶|进入|处于|带有|附加).{0,10}"
                  r"(击飞|倒地|冻结|燃烧|导电|腐蚀|附着|眩晕|异常|状态|结晶|破防)", 0.7),
    ("on_heal", r"(受到治疗|接受治疗|治疗触发)", 0.7),
    ("on_switch", r"(切换|登场|上场|替换)", 0.7),
    ("on_skill_hit", r"(战技|连携技).{0,8}(命中|造成)", 0.6),
    ("on_combo", r"(其他干员的连携|连携技触发后)", 0.6),
]

# 只有这些槽位的技能才"需要条件才能放"
CONDITIONAL_SLOTS = {"combo_skill"}

ABNORMAL_RULES: list[tuple[str, str]] = [
    ("燃烧", r"燃烧"),
    ("导电", r"导电"),
    ("冻结", r"(冻结|冰冻)"),
    ("腐蚀", r"腐蚀"),
    ("击飞", r"击飞"),
    ("倒地", r"倒地"),
    ("猛击", r"猛击"),
    ("碎甲", r"碎甲"),
    ("附着", r"(附着|蓄积)"),
]

SLOT_DEFAULTS = {
    "battle_skill": ("none", 0.85),
    "ultimate": ("none", 0.85),
    "combo_skill": ("unknown", 0.3),
    "normal_attack": ("none", 0.8),
    "power_attack": ("none", 0.8),
    "dash_attack": ("none", 0.6),
    "plunging_attack": ("none", 0.6),
}


def _first_match(rules, text: str):
    for name, pat, conf in rules:
        m = re.search(pat, text)
        if m:
            return name, m, conf
    return None, None, 0.0


def _duration(text: str) -> tuple[float | None, bool]:
    """返回 (时长秒, 是否 DoT/持续型)。"""
    dot = bool(re.search(r"(持续.{0,6}伤害|每秒|每跳|持续对敌人造成)", text))
    m = re.search(r"持续\s*(\d+(?:\.\d+)?)\s*秒", text)
    if m:
        return float(m.group(1)), dot
    m = re.search(r"(\d+(?:\.\d+)?)\s*秒", text)
    if m:
        return float(m.group(1)), dot
    if re.search(r"持续数秒|接下来一段时间", text):
        return None, True
    return None, dot


def _target(text: str) -> str:
    if re.search(r"(附近|范围).{0,4}(干员|友方)", text) or "全队" in text:
        return "team"
    if re.search(r"(自身|自己)", text):
        return "self"
    if re.search(r"(范围内|区域|地带|周围)", text):
        return "area"
    if re.search(r"(敌人|目标|敌方)", text):
        return "enemy"
    return "unknown"


def _hp_threshold(text: str) -> float | None:
    m = re.search(r"生命值\s*(?:不高于|低于|少于)\s*(\d+)\s*%", text)
    return float(m.group(1)) if m else None


def extract_skill(skill: dict) -> dict[str, Any]:
    """skill 是 normalized 的 skill dict（含 slot / element / semantics.text_zh）。"""
    slot = skill.get("slot") or "unknown"
    text = ((skill.get("semantics") or {}).get("text_zh") or "")
    if not text:
        text = skill.get("name_zh") or ""

    notes: list[str] = []
    trig, _m, conf_trig = _first_match(TRIGGER_RULES, text)
    if slot in CONDITIONAL_SLOTS:
        if trig is None:
            trig, conf_trig = ("unknown", 0.2)
            notes.append("连携技未识别出触发条件，需人工/LLM 复核")
    else:
        # 非连携技：本身随时可放，但描述里的"受到攻击"仍是重要机制标记
        trig, conf_trig = "none", 0.85

    element = skill.get("element")
    if not element:
        for w in ELEMENT_WORDS:
            if w in text:
                element = w
                break

    abnormal = None
    for name, pat in ABNORMAL_RULES:
        if re.search(pat, text):
            abnormal = name
            break
    # 物理异常 vs 法术异常：物理异常词优先
    if slot == "combo_skill" and abnormal in ("击飞", "倒地", "猛击", "碎甲"):
        notes.append("物理异常")

    duration, is_dot = _duration(text)
    hp = _hp_threshold(text)
    target = _target(text)

    needs_hit = trig == "on_hit_taken" or bool(
        re.search(r"(受到攻击|受击|格挡)", text))
    heals = bool(re.search(r"(治疗|回复.{0,4}生命值)", text))
    shields = bool(re.search(r"(护盾|庇护|格挡)", text))

    conf = conf_trig
    if element:
        conf = max(conf, 0.8) if text else conf
    if not text:
        conf = min(conf, 0.3)
        notes.append("无描述文本")

    obj: dict[str, Any] = {
        "slot": slot,
        "trigger": trig,
        "trigger_params": {
            "hp_below_pct": hp,
            "requires_status": abnormal if trig == "on_status" else None,
            "window_s": None,
        },
        "element": element,
        "abnormal": abnormal,
        "target": target,
        "duration_s": duration,
        "is_dot": is_dot,
        "needs_enemy_hit": needs_hit,
        "gives_sp": None,
        "gives_ult": None,
        "heals": heals,
        "shields": shields,
        "tags": [],
        "text_zh": text or None,
        "confidence": round(conf, 2),
        "method": "rule",
        "notes": notes,
    }
    ok, errs = validate_semantics(obj)
    if not ok:
        obj["notes"].extend([f"schema: {e}" for e in errs])
        obj["confidence"] = min(obj["confidence"], 0.3)
    return obj


def extract_operator(op: Any) -> dict[str, Any]:
    """对 Operator（model.Operator）里所有技能跑规则，返回 {skill_key: semantics}。"""
    out: dict[str, Any] = {}
    for key, sk in op.skills.items():
        sd = sk.to_dict()
        out[key] = extract_skill(sd)
    return out
