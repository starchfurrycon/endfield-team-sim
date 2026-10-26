"""人工核对过的修正（curated overrides）。

数据源的现实：客户端表不带"技能属性"，编辑口径的中文 wiki 又可能缺页
（例如噗切娜没有 BWIKI 页面），于是少数技能的元素/职业只能靠人工核对。

规则：
  * 只放**证据明确**的修正，每条必须带 note + sources；
  * 优先级最高（wiki → 客户端表 → 默认），构建时应用；
  * `check` 会把生效的修正列进报告，避免"悄悄改数据"。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("curated.json")


@lru_cache(maxsize=1)
def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def spec_for(char_id: str) -> dict:
    return ((load().get("operators") or {}).get(char_id)) or {}


def apply_to_operator(op) -> list[str]:
    """把修正应用到 Operator（原地），返回人类可读的变更说明。"""
    spec = spec_for(op.char_id)
    if not spec:
        return []
    notes: list[str] = []
    elem = spec.get("element")
    if elem and elem != op.element:
        notes.append(f"element {op.element} → {elem}")
        op.element = elem
        op.sources["element"] = "curated"
    for slot, val in (spec.get("skill_elements") or {}).items():
        for sk in op.skills.values():
            if sk.slot == slot and sk.element != val:
                notes.append(f"{slot} element {sk.element} → {val}")
                sk.element = val
    if spec.get("profession") and spec["profession"] != op.profession:
        notes.append(f"profession {op.profession} → {spec['profession']}")
        op.profession = spec["profession"]
    if spec.get("rarity") and int(spec["rarity"]) != op.rarity:
        notes.append(f"rarity {op.rarity} → {spec['rarity']}")
        op.rarity = int(spec["rarity"])
    if notes:
        op.sources["curated"] = "src/endfield/data/curated.json"
    return notes
