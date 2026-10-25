"""LLM 语义增强（可选通道）。

定位（计划 §1.2）：LLM 只做**分类与解释**，不产出任何数值倍率。
  1. 输入：技能中文描述 + 槽位 + 元素（来自数据表）
  2. 输出：符合 schema 的语义对象（枚举/布尔/时长）
  3. 校验失败 → 直接丢弃，回退规则结果（绝不"尽力解析"）

无 API key 时 `available()` 返回 False，全流程降级且不报错。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .. import config
from .schema import ABNORMALS, SEMANTICS_SCHEMA, TRIGGERS, validate_semantics

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _cfg() -> dict:
    return {
        "base": (os.environ.get("ENDFIELD_LLM_BASE") or DEFAULT_BASE).rstrip("/"),
        "key": os.environ.get("ENDFIELD_LLM_API_KEY") or "",
        "model": os.environ.get("ENDFIELD_LLM_MODEL") or DEFAULT_MODEL,
    }


def available() -> bool:
    return bool(_cfg()["key"])


def _cache_key(payload: dict, model: str) -> Path:
    h = hashlib.sha256(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + model).encode()
    ).hexdigest()[:32]
    return config.LLM_CACHE / f"sem_{h}.json"


SYSTEM_PROMPT = """你是《明日方舟：终末地》的技能语义标注器。
你只输出 JSON，不输出任何解释性文字。绝对不要输出伤害倍率、数值加成——
数值一律由数据表提供，你只负责判断"机制类别"。

字段要求：
- trigger: 技能的发动条件，只能是这些之一：
  none / on_hit_taken / on_heavy_attack / on_dodge / on_execute / on_heal /
  on_switch / on_skill_hit / on_status / on_break / on_combo / unknown
  连携技务必判断触发条件是"主控受击"还是"重击命中"还是"敌人处于某状态"。
- element: 物理/灼热/电磁/寒冷/自然/超域 或 null
- abnormal: 燃烧/导电/冻结/腐蚀/击飞/倒地/猛击/碎甲/附着 或 null
- target: self/team/enemy/area/unknown
- duration_s: 持续时间（秒，数字）或 null
- is_dot: 是否持续伤害
- needs_enemy_hit: 是否必须敌人攻击/受击才能生效
- heals / shields: 布尔
- confidence: 0~1
- method: "llm"
"""


def _prompt(skill: dict) -> dict:
    return {
        "slot": skill.get("slot"),
        "element_hint": skill.get("element"),
        "name": skill.get("name_zh"),
        "description": (skill.get("semantics") or {}).get("text_zh"),
    }


def annotate_skill(skill: dict, *, use_cache: bool = True) -> dict | None:
    if not available():
        return None
    cfg = _cfg()
    payload = _prompt(skill)
    if not payload.get("description"):
        return None
    cache = _cache_key(payload, cfg["model"])
    if use_cache and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            ok, _ = validate_semantics(cached)
            if ok:
                return cached
        except Exception:  # noqa: BLE001
            pass

    body = {
        "model": cfg["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['key']}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8"))
        txt = resp["choices"][0]["message"]["content"]
        sem = json.loads(txt)
    except Exception:  # noqa: BLE001
        return None

    sem.setdefault("slot", skill.get("slot"))
    sem.setdefault("method", "llm")
    sem.setdefault("confidence", 0.6)
    sem.setdefault("trigger_params", {})
    sem.setdefault("tags", [])
    sem.setdefault("notes", [])
    ok, errs = validate_semantics(sem)
    if not ok:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(sem, ensure_ascii=False, indent=1), encoding="utf-8")
    return sem


def merge(rule_sem: dict, llm_sem: dict | None) -> dict:
    """规则为主、LLM 补空。两者冲突时保留规则值并降低置信度。"""
    if not llm_sem:
        return rule_sem
    out = dict(rule_sem)
    conflicts: list[str] = []
    for k in ("trigger", "abnormal", "target", "element"):
        rv = rule_sem.get(k)
        lv = llm_sem.get(k)
        if rv in (None, "unknown", "none") and lv not in (None, "unknown"):
            out[k] = lv
        elif rv and lv and rv != lv:
            conflicts.append(f"{k}: rule={rv} llm={lv}")
    for k in ("duration_s", "is_dot", "needs_enemy_hit", "heals", "shields"):
        if out.get(k) in (None, False) and llm_sem.get(k) not in (None, False):
            out[k] = llm_sem[k]
    out["method"] = "merged"
    if conflicts:
        out["notes"] = list(out.get("notes") or []) + ["conflict " + c for c in conflicts]
        out["confidence"] = round(min(out.get("confidence", 0.5), 0.5), 2)
    else:
        out["confidence"] = round(max(out.get("confidence", 0.0),
                                     llm_sem.get("confidence", 0.0)), 2)
    return out


def describe_options() -> str:
    return (f"LLM: {'可用' if available() else '未配置（将只使用规则通道）'}\n"
            f"  枚举 trigger: {', '.join(TRIGGERS)}\n"
            f"  枚举 abnormal: {', '.join(str(x) for x in ABNORMALS)}\n"
            f"  schema 字段: {', '.join(SEMANTICS_SCHEMA['properties'])}")
