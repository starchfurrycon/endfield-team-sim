"""装备 / 武器推荐。

数据来源（均已由 build 导出到 data/normalized/）：
  * weapons.json —— 每把武器的稀有度、类型、1~90 级 ATK、被动黑板（满基质 9/9/4）
  * equip.json   —— 每件装备的属性向量（精锻 0~3）

推荐逻辑（可解释，不做玄学）：
  武器：同类型内按「等效 ATK = ATK×(1+ATK%) + 主属性×0.005×角色ATK」排序，
        取前 N（默认 3）。
  装备：两个方向各出一套 4 件——
        · 输出：主属性 + 伤害加成权重
        · 功能：生命 + 防御 + 副属性权重
        同一件不会被两套同时选中（分工不同才换装）。
"""

from __future__ import annotations

import json
from functools import lru_cache

from .. import config
from ..model import Dataset, Operator


@lru_cache(maxsize=1)
def _weapons() -> dict:
    p = config.NORM / "weapons.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


@lru_cache(maxsize=1)
def _equip() -> dict:
    p = config.NORM / "equip.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("items") or {}
    except Exception:  # noqa: BLE001
        return {}


_DMG_ATTRS = {17, 28, 29, 32, 33, 50, 51, 52, 53, 54, 61, 64, 65, 66, 67, 68, 69}
_MAIN_ATTR_ID = {"力量": 39, "敏捷": 40, "智识": 41, "意志": 42}


def _weapon_type(op: Operator) -> int | None:
    w = _weapons().get(op.default_weapon_id or "")
    return (w or {}).get("weapon_type")


def recommend_weapons(ds: Dataset, cid: str, top: int = 3,
                      weapon_level: int = 90) -> list[dict]:
    op = ds.operators.get(cid)
    if not op:
        return []
    wt = _weapon_type(op)
    row = op.stat_at(90)
    char_atk = float(row.get("atk") or 0.0)
    out = []
    for wid, w in _weapons().items():
        if wt is not None and w.get("weapon_type") != wt:
            continue
        curve = w.get("atk_by_level") or []
        if not curve:
            continue
        idx = max(0, min(int(weapon_level), len(curve)) - 1)
        atk = float(curve[idx])
        bb = w.get("passive_bb") or {}
        atk_pct = float(bb.get("atk") or 0.0)
        main = float(bb.get("mainattr") or 0.0)
        eff = atk * (1.0 + atk_pct) + main * 0.005 * char_atk
        out.append({"weapon_id": wid, "name": w.get("name") or wid,
                    "rarity": w.get("rarity"),
                    "atk": atk, "atk_pct": round(atk_pct, 4),
                    "mainattr": main, "score": round(eff, 1)})
    out.sort(key=lambda x: (-x["score"], -(x["rarity"] or 0)))
    return out[:top]


def _piece_score(attrs: dict, mode: str, main_id: int) -> float:
    s = 0.0
    for k, vals in attrs.items():
        v = float(vals[-1]) if vals else 0.0
        try:
            a = int(k)
        except ValueError:
            continue
        if mode == "output":
            if a == main_id:
                s += v * 3.0
            elif a in _DMG_ATTRS:
                s += v * 400.0
            elif a in (39, 40, 41, 42):
                s += v * 1.0
        else:  # sustain
            if a == main_id:
                s += v * 1.0
            elif a in (1, 3):
                s += v * 0.6
            elif a in (39, 40, 41, 42):
                s += v * 1.5
    return s


def recommend_equipment(ds: Dataset, cid: str, top: int = 2,
                        level: int = 3) -> list[dict]:
    op = ds.operators.get(cid)
    if not op:
        return []
    main_id = _MAIN_ATTR_ID.get(op.main_attr or "", 0)
    items = _equip()
    sets = []
    used: set[str] = set()
    for mode, label in (("output", "输出向"), ("sustain", "功能向")):
        ranked = []
        for iid, row in items.items():
            if iid in used:
                continue
            sc = _piece_score(row.get("attrs") or {}, mode, main_id)
            if sc > 0:
                ranked.append((sc, iid))
        ranked.sort(reverse=True)
        limit = {"body": 1, "hand": 1, "edc": 2}
        chosen = []
        for _, iid in ranked:
            slot = ("body" if "_body_" in iid else
                    "hand" if "_hand_" in iid else
                    "edc" if "_edc_" in iid else "other")
            if slot == "other" or limit.get(slot, 0) <= 0:
                continue
            limit[slot] -= 1
            chosen.append(iid)
            if len(chosen) == 4:
                break
        used.update(chosen)
        names = [(items.get(iid) or {}).get("name") or iid for iid in chosen]
        sets.append({
            "mode": mode, "label": label,
            "pieces": chosen,
            "piece_names": names,
            "score": round(sum(s for s, _ in ranked[:4]), 1),
            "enhance_level": level,
        })
        if len(sets) >= top:
            break
    return sets


def recommend_for_team(ds: Dataset, team: list[str], top_weapons: int = 3,
                       top_sets: int = 2) -> dict:
    out = {}
    for cid in team:
        op = ds.operators.get(cid)
        if not op:
            continue
        out[cid] = {
            "name": op.name_zh,
            "weapons": recommend_weapons(ds, cid, top=top_weapons),
            "equipment": recommend_equipment(ds, cid, top=top_sets),
        }
    return out


def apply_presets(ds: Dataset, sc, equip: str = "none", weapon: int = 0,
                  equip_level: int = 3) -> list[str]:
    """把「装备/武器预设」落到 Scenario（原地修改），返回人类可读的说明。

    没有预设时什么都不做 —— 保持"默认空装备、默认武器"的旧行为。
    """
    notes: list[str] = []
    equip = (equip or "none").strip()
    if equip in ("output", "sustain"):
        lv = max(0, min(int(equip_level), 3))
        sc.equip_level = lv
        got = 0
        for cid in sc.team:
            for row in recommend_equipment(ds, cid, top=2):
                if row.get("mode") == equip:
                    sc.equip_by_op[cid] = list(row.get("pieces") or [])
                    got += 1
                    break
        label = "输出向" if equip == "output" else "功能向"
        notes.append(f"{label}套装（精锻 {lv}）×{got} 人")
    wi = int(weapon or 0)
    if wi > 0:
        picked = 0
        for cid in sc.team:
            rows = recommend_weapons(ds, cid, top=max(wi, 3))
            if len(rows) >= wi:
                sc.weapon_by_op[cid] = rows[wi - 1]["weapon_id"]
                picked += 1
        notes.append(f"推荐武器第 {wi} 顺位 ×{picked} 人")
    return notes
