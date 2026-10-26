"""语义 JSON Schema（封闭枚举）。

未知值必须落到 "unknown" / null，不允许静默丢弃——否则引擎会在不自知的情况下
把"有条件的连携"当成"无条件可用"。
"""

from __future__ import annotations

from typing import Any

TRIGGERS = [
    "none",            # 无条件（战技/终结技通常如此）
    "on_hit_taken",    # 受到攻击后 ← 用户点名的"依赖受击"
    "on_heavy_attack", # 重击命中后
    "on_dodge",        # 闪避/完美闪避后
    "on_execute",      # 处决后
    "on_heal",         # 治疗触发
    "on_switch",       # 切换/登场
    "on_skill_hit",    # 战技命中后
    "on_status",       # 敌人处于某状态时
    "on_break",        # 敌人失衡时
    "on_combo",        # 由其他连携触发
    "unknown",
]

SLOTS = ["normal_attack", "power_attack", "battle_skill", "combo_skill",
         "ultimate", "dash_attack", "plunging_attack", "execute"]

ABNORMALS = ["燃烧", "导电", "冻结", "腐蚀", "击飞", "倒地", "猛击", "碎甲",
             "附着", None]

TARGETS = ["self", "team", "enemy", "area", "unknown"]

ELEMENTS = ["物理", "灼热", "电磁", "寒冷", "自然", "超域", None]

ROLES = ["damage", "heal", "buff", "utility", "unknown"]

SEMANTICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["slot", "trigger", "confidence", "method"],
    "additionalProperties": False,
    "properties": {
        "slot": {"enum": SLOTS},
        "trigger": {"enum": TRIGGERS},
        "trigger_params": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "hp_below_pct": {"type": ["number", "null"]},
                "requires_status": {"type": ["string", "null"]},
                "window_s": {"type": ["number", "null"]},
            },
        },
        "element": {"enum": ELEMENTS},
        "abnormal": {"enum": ABNORMALS},
        "target": {"enum": TARGETS},
        "role": {"enum": ROLES},
        "duration_s": {"type": ["number", "null"]},
        "is_dot": {"type": "boolean"},
        "needs_enemy_hit": {"type": "boolean"},
        "gives_sp": {"type": ["number", "null"]},
        "gives_ult": {"type": ["number", "null"]},
        "heals": {"type": "boolean"},
        "shields": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "text_zh": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "method": {"enum": ["rule", "llm", "manual", "merged"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

_ENUM = {
    "trigger": TRIGGERS,
    "slot": SLOTS,
    "abnormal": ABNORMALS,
    "target": TARGETS,
    "role": ROLES,
    "element": ELEMENTS,
    "method": ["rule", "llm", "manual", "merged"],
}


def validate_semantics(obj: dict) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return False, ["not an object"]
    for key in SEMANTICS_SCHEMA["required"]:
        if key not in obj:
            errs.append(f"missing {key}")
    for key, allowed in _ENUM.items():
        if key in obj and obj[key] not in allowed:
            errs.append(f"{key}={obj[key]!r} not in enum")
    for key in ("confidence",):
        v = obj.get(key)
        if not isinstance(v, (int, float)) or not (0 <= v <= 1):
            errs.append(f"{key} out of range")
    for key in ("is_dot", "needs_enemy_hit", "heals", "shields"):
        if key in obj and not isinstance(obj[key], bool):
            errs.append(f"{key} not bool")
    ev = obj.get("evidence")
    if ev is not None and (not isinstance(ev, list)
                           or any(not isinstance(x, str) for x in ev)):
        errs.append("evidence must be array[str]")
    return (not errs), errs

