"""装备 / 武器推荐：完整套装 + 专武优先 + 固定定位 + 模拟校验。

数据来源（build 导出到 data/normalized/）：
  * weapons.json —— 武器类型、90 级 ATK、满基质被动黑板
  * equip.json   —— 每件装备属性向量 + 套装被动

推荐逻辑：
  1. 每个干员先定定位（主 C / 辅助 / 生存 / 治疗），不再分"输出/功能"两套；
  2. 武器：专武（curated）> 大月卡下位专武 > 被动折算后的候选，
     再按队伍模拟伤害做一次坐标下降；
  3. 装备：只枚举**能触发套装被动**的 3/4 件组合，整队一起模拟，
     每个干员固定一套；
  4. 枚举空间大，所以用"坐标下降"：固定其他人，逐个换武器/套装，
     只接受队伍总伤提升的方案（缓存相同配置）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .. import config
from ..model import Dataset, Operator
from ..engine.battle import Scenario
from .rotations import search_rotation


@lru_cache(maxsize=1)
def _weapons() -> dict:
    try:
        return json.loads((config.NORM / "weapons.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


@lru_cache(maxsize=1)
def _equip() -> dict:
    try:
        return (json.loads((config.NORM / "equip.json").read_text(encoding="utf-8"))
                or {})
    except Exception:  # noqa: BLE001
        return {"items": {}, "suits": {}}


@lru_cache(maxsize=1)
def _curated() -> dict:
    p = Path(__file__).resolve().parents[1] / "data" / "loadout_curated.json"
    if not p.exists():
        p = config.NORM / "loadout_curated.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


_DMG_ATTRS = {17, 28, 29, 32, 33, 50, 51, 52, 53, 54, 61, 64, 65, 66, 67, 68, 69}
_MAIN_ATTR_ID = {"力量": 39, "敏捷": 40, "智识": 41, "意志": 42}
_MAIN_ATTR_KEY = {"力量": "str", "敏捷": "agi", "智识": "wisd", "意志": "will"}
_SLOT = ("body", "hand", "edc", "edc")
_SLOT_CN = {"body": "护甲", "hand": "护手", "edc": "配件"}


def _role(op: Operator) -> str:
    tags = set(op.battle_tags or [])
    if tags & {"治疗", "回复", "护盾"}:
        return "healer" if tags & {"治疗", "回复"} else "tank"
    if tags & {"防护", "生存"}:
        return "tank"
    if tags & {"大量伤害", "输出", "爆发"}:
        return "dps"
    if tags & {"辅助", "控制", "增益"}:
        return "support"
    if (op.profession or "") in ("突击", "狙击", "术师"):
        return "dps"
    return "support"


def _role_cn(role: str) -> str:
    return {"dps": "主 C", "support": "辅助", "tank": "生存",
            "healer": "治疗"}.get(role, role)


def _weapon_type(op: Operator) -> int | None:
    w = _weapons().get(op.default_weapon_id or "")
    return (w or {}).get("weapon_type")


def _weapon_static(w: dict, op: Operator, level: int = 90) -> float:
    curve = w.get("atk_by_level") or []
    if not curve:
        return 0.0
    idx = max(0, min(int(level), len(curve)) - 1)
    atk = float(curve[idx])
    bb = w.get("passive_bb") or {}
    char_atk = float(op.stat_at(90).get("atk") or 0.0)
    main_key = _MAIN_ATTR_KEY.get(op.main_attr or "", "")
    val = float(bb.get("mainattr") or 0.0)
    if main_key:
        val += float(bb.get(main_key) or 0.0)
    eff = atk + val * 0.005 * char_atk
    eff *= 1.0 + float(bb.get("atk") or 0.0)
    # 被动里未进引擎的伤害/暴击词条，做等效折算（标注为估算）
    eff += float(bb.get("crirate") or 0.0) * 900
    eff += float(bb.get("critdmg") or 0.0) * 600
    for key in ("crystdam", "firedam", "spelldam", "phy_dmg", "nature_dmg"):
        eff += float(bb.get(key) or 0.0) * 700
    if w.get("rarity"):
        eff += float(w["rarity"]) * 2
    return eff


def _resolve_weapon(name: str) -> str | None:
    if not name:
        return None
    for wid, w in _weapons().items():
        if (w.get("name") or "") == name:
            return wid
    return None


def recommend_weapons(ds: Dataset, cid: str, top: int = 3,
                      weapon_level: int = 90) -> list[dict]:
    op = ds.operators.get(cid)
    if not op:
        return []
    wt = _weapon_type(op)
    cur = (_curated().get(op.name_zh or "") or {})
    out = []
    for wid, w in _weapons().items():
        if wt is not None and w.get("weapon_type") != wt:
            continue
        if not (w.get("atk_by_level") or []):
            continue
        out.append({
            "weapon_id": wid, "name": w.get("name") or wid,
            "rarity": w.get("rarity"), "atk": 0,
            "atk_pct": round(float((w.get("passive_bb") or {}).get("atk") or 0.0), 4),
            "mainattr": float((w.get("passive_bb") or {}).get("mainattr") or 0.0),
            "score": round(_weapon_static(w, op, weapon_level), 1),
        })
    for r in out:
        r["atk"] = int((_weapons()[r["weapon_id"]].get("atk_by_level") or [0])[
            max(0, min(weapon_level, len(_weapons()[r["weapon_id"]]["atk_by_level"])) - 1)])
    insig = _resolve_weapon(cur.get("signature", ""))
    budget = _resolve_weapon(cur.get("budget", ""))
    for r in out:
        if r["weapon_id"] == insig:
            r["tier"] = "专武"
            r["score"] = round(r["score"] * 1.18, 1)
        elif r["weapon_id"] == budget:
            r["tier"] = "大月卡"
            r["score"] = round(r["score"] * 1.08, 1)
        else:
            r["tier"] = ""
    # 专武/大月卡优先（引擎还没建模的被动，用 curated 先验保证不误排）
    out.sort(key=lambda r: (0 if r["tier"] == "专武" else 1 if r["tier"] == "大月卡" else 2,
                            -r["score"], -(r["rarity"] or 0)))
    return out[:top]


def _suit_name(sid: str, srow: dict) -> str:
    for iid in (srow.get("pieces") or []):
        nm = ((_equip().get("items") or {}).get(iid) or {}).get("name") or ""
        if nm:
            return nm.split("·")[0]
    return sid


def _piece_score(row: dict, main_id: int, role: str) -> float:
    s = 0.0
    for k, vals in (row.get("attrs") or {}).items():
        v = float(vals[-1]) if vals else 0.0
        try:
            a = int(k)
        except ValueError:
            continue
        if role == "dps":
            if a == main_id:
                s += v * 2.5
            elif a in _DMG_ATTRS:
                s += v * 300.0
            elif a in (39, 40, 41, 42):
                s += v * 1.0
        elif role in ("tank", "healer"):
            if a in (1, 3):
                s += v * 1.2
            elif a in (39, 40, 41, 42):
                s += v * 1.0
        else:
            if a == main_id:
                s += v * 1.5
            elif a in _DMG_ATTRS:
                s += v * 80.0
            elif a in (39, 40, 41, 42):
                s += v * 1.2
    return s


def _suit_score(srow: dict, role: str, op: Operator | None = None) -> float:
    bb = srow.get("passive_bb") or {}
    s = 0.0
    for k, v in bb.items():
        v = float(v or 0.0)
        if v > 1.0 and (k in ("dmg_up", "atk_up", "crit_up", "crit_up2")
                        or k.endswith("_dmg_up")):
            v /= 100.0
        if k in ("crit_up", "crit_up2") and op is not None and "暴击" in (op.battle_tags or []):
            s += v * 600.0
        elif k in ("dmg_up", "atk_up", "crit_up", "crit_up2"):
            s += v * (100.0 if role == "dps" else 25.0)
        elif k.endswith("_dmg_up"):
            elem = (op.element if op is not None else "") or ""
            pairs = {"寒冷": ("cryst", "cold"), "灼热": ("fire",),
                     "电磁": ("pulse", "elec"), "自然": ("nature",),
                     "物理": ("phy",)}
            if any(x in k for x in pairs.get(elem, ())):
                s += v * (100.0 if role == "dps" else 25.0)
        elif k in ("hp_ratio", "dmg_taken_down"):
            s += v * (100.0 if role in ("tank", "healer") else 10.0)
        elif k in ("comboskill_cooldown",):
            s += abs(v) * 20.0
        elif k in ("hp_up", "def_up", "heal_up"):
            s += v * (0.2 if role in ("tank", "healer") else 0.0)
    return s


def _set_effect_text(srow: dict) -> str:
    bb = srow.get("passive_bb") or {}
    names = {
        "dmg_up": "伤害", "atk_up": "攻击", "crit_up": "暴击", "crit_up2": "暴击",
        "hp_ratio": "生命", "dmg_taken_down": "减伤", "skill_dmg_up": "战技伤害",
        "spell_dmg_up": "法术伤害", "phy_dmg_up": "物理伤害",
        "crystdam_up": "寒冷伤害", "comboskill_cooldown": "连携冷却",
    }
    parts = []
    for k, v in bb.items():
        if k in ("duration", "duration2", "max_stack", "stack_cond"):
            continue
        v = float(v or 0.0)
        if not v:
            continue
        label = names.get(k) or names.get(k.rstrip("_up") + "_up") or k
        if abs(v) < 1.0:
            parts.append(f"{label}+{v:.0%}")
        else:
            parts.append(f"{label}+{v:g}")
    return " / ".join(parts[:3])


def _set_pieces(sid: str, srow: dict, items: dict, main_id: int,
                role: str) -> list[str]:
    """从套装里选 need 件（优先能触发套装），再补满 4 个槽位。"""
    need = int(srow.get("need", 3))
    by_slot: dict[str, list[tuple[float, str]]] = {}
    for iid in (srow.get("pieces") or []):
        slot = ("body" if "_body_" in iid else "hand" if "_hand_" in iid
                else "edc" if "_edc_" in iid else "other")
        if slot == "other":
            continue
        by_slot.setdefault(slot, []).append(
            (_piece_score(items.get(iid) or {}, main_id, role), iid))
    chosen: list[str] = []
    for slot in ("body", "hand", "edc", "edc"):
        if len(chosen) >= need:
            break
        pool = by_slot.get(slot) or []
        if pool:
            pool.sort(reverse=True)
            chosen.append(pool[0][1])
            pool.pop(0)
    # 补满第四个槽：先补同套第 4 件，再跨套
    if len(chosen) < 4:
        rest = [iid for iid in (srow.get("pieces") or [])
                if iid not in chosen and "_edc_" in iid]
        rest += [iid for iid in items
                 if iid not in chosen and iid not in (srow.get("pieces") or [])
                 and any(f"_{s}_" in iid for s in ("body", "hand", "edc"))]
        def _prio(iid: str) -> float:
            same = iid in (srow.get("pieces") or [])
            return _piece_score(items.get(iid) or {}, main_id, role) + (30 if same else 0)
        rest.sort(key=_prio, reverse=True)
        for iid in rest:
            if len(chosen) >= 4:
                break
            slot = ("body" if "_body_" in iid else "hand" if "_hand_" in iid else "edc")
            used = sum(1 for x in chosen if f"_{slot}_" in x)
            cap = 1 if slot in ("body", "hand") else 2
            if used < cap:
                chosen.append(iid)
    return chosen[:4]


def recommend_equipment(ds: Dataset, cid: str, top: int = 1,
                        level: int = 3) -> list[dict]:
    op = ds.operators.get(cid)
    if not op:
        return []
    role = _role(op)
    main_id = _MAIN_ATTR_ID.get(op.main_attr or "", 0)
    items = _equip().get("items") or {}
    suits = _equip().get("suits") or {}
    ranked = sorted(suits.items(), key=lambda kv: _suit_score(kv[1], role, op), reverse=True)
    out = []
    for sid, srow in ranked[:max(top, 1)]:
        pieces = _set_pieces(sid, srow, items, main_id, role)
        if not pieces:
            continue
        count = sum(1 for iid in pieces if iid in (srow.get("pieces") or []))
        if count < int(srow.get("need", 3)):
            continue
        names = [(items.get(iid) or {}).get("name") or iid for iid in pieces]
        out.append({
            "mode": role, "label": _role_cn(role), "role": role,
            "set_id": sid, "set_name": _suit_name(sid, srow),
            "set_count": count, "need": int(srow.get("need", 3)),
            "pieces": pieces, "piece_names": names,
            "set_effect": _set_effect_text(srow),
            "score": round(_suit_score(srow, role, op), 1),
            "enhance_level": level,
        })
        if len(out) >= top:
            break
    return out


def _score_team(ds: Dataset, team: list[str], weapons: dict, equips: dict,
                duration_s: float = 40.0) -> float:
    sc = Scenario(team=team, duration_s=duration_s,
                  weapon_by_op=dict(weapons), equip_by_op={k: list(v) for k, v in equips.items()},
                  equip_level=3)
    r = search_rotation(ds, sc, beam=1, steps=10)
    return float(r.result.total_damage)


def optimize_team(ds: Dataset, team: list[str], duration_s: float = 40.0,
                  rounds: int = 2) -> dict:
    """坐标下降：固定其他人，逐个换武器/套装，取队伍总伤最高。"""
    op_roles = {cid: _role(ds.operators[cid]) for cid in team if cid in ds.operators}
    weapons: dict[str, str] = {}
    equips: dict[str, list[str]] = {}
    for cid in team:
        ws = recommend_weapons(ds, cid, top=4)
        weapons[cid] = ws[0]["weapon_id"] if ws else ""
        es = recommend_equipment(ds, cid, top=1)
        equips[cid] = list(es[0]["pieces"]) if es else []
    cache: dict[tuple, float] = {}

    def score() -> float:
        key = (tuple(sorted(weapons.items())),
               tuple(sorted((k, tuple(v)) for k, v in equips.items())))
        if key not in cache:
            cache[key] = _score_team(ds, team, weapons, equips, duration_s)
        return cache[key]

    best = score()
    for _ in range(max(1, rounds)):
        changed = False
        for cid in team:
            olds = weapons[cid]
            for row in recommend_weapons(ds, cid, top=4):
                if row["weapon_id"] == olds:
                    continue
                weapons[cid] = row["weapon_id"]
                s = score()
                if s > best + 1e-6:
                    best, olds, changed = s, row["weapon_id"], True
            weapons[cid] = olds
            olde = list(equips[cid])
            for row in recommend_equipment(ds, cid, top=4):
                equips[cid] = list(row["pieces"])
                s = score()
                if s > best + 1e-6:
                    best, olde, changed = s, list(row["pieces"]), True
            equips[cid] = olde
        if not changed:
            break

    out = {}
    for cid in team:
        op = ds.operators.get(cid)
        if not op:
            continue
        ws = recommend_weapons(ds, cid, top=4)
        wsel = next((w for w in ws if w["weapon_id"] == weapons[cid]), ws[0])
        es = recommend_equipment(ds, cid, top=1)
        out[cid] = {
            "name": op.name_zh, "role": op_roles.get(cid, "support"),
            "role_cn": _role_cn(op_roles.get(cid, "support")),
            "weapons": ws,
            "weapon": wsel,
            "equipment": es,
        }
    return {"roles": out, "damage": round(best, 1),
            "method": "coordinate-descent + simulation"}


def recommend_for_team(ds: Dataset, team: list[str], top_weapons: int = 3,
                       top_sets: int = 1, optimize: bool = True,
                       duration_s: float = 40.0) -> dict:
    if optimize:
        opt = optimize_team(ds, team, duration_s=duration_s)
        return opt["roles"]
    out = {}
    for cid in team:
        op = ds.operators.get(cid)
        if not op:
            continue
        out[cid] = {"name": op.name_zh, "role": _role(op),
                    "role_cn": _role_cn(_role(op)),
                    "weapons": recommend_weapons(ds, cid, top=top_weapons),
                    "equipment": recommend_equipment(ds, cid, top=top_sets)}
    return out


def apply_presets(ds: Dataset, sc, equip: str = "none", weapon: int = 0,
                  equip_level: int = 3) -> list[str]:
    """兼容旧接口：预设落到 Scenario。数据仍用固定的一套。"""
    notes: list[str] = []
    equip = (equip or "none").strip()
    if equip in ("output", "sustain", "best"):
        lv = max(0, min(int(equip_level), 3))
        sc.equip_level = lv
        got = 0
        for cid in sc.team:
            rows = recommend_equipment(ds, cid, top=1)
            if rows:
                sc.equip_by_op[cid] = list(rows[0].get("pieces") or [])
                got += 1
        notes.append(f"整套装备（精锻 {lv}）×{got} 人")
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
