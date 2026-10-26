"""raw → normalized 构建。

输入：data/raw/github/*（表 + 技能帧数据）、data/raw/wiki/*（描述/索引）
输出：data/normalized/operators/<char_id>.json、dataset.json、manifest.json
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .. import config
from ..model import Hit, Operator, Skill, SkillLevel, Timing
from . import github, wiki
from .coverage import crosscheck_skilldata as cov_crosscheck

CACHE = config.RAW / "github"

# 属性 id -> 字段名（AttributeMetaTable / wiki 数值范围）
_STAT_IDS = {
    0: "level",
    1: "max_hp", 2: "atk", 3: "def",
    39: "str", 40: "agi", 41: "wisd", 42: "will",
    9: "crit_rate", 10: "crit_dmg",
    87: "arts_intensity",   # 源石技艺强度
}

_WEAPON_NAME = {
    "sword": "长剑", "lance": "长枪", "pistol": "手枪",
    "claym": "双手剑", "funnel": "法器",
}

_ATTR_ZH = {39: "力量", 40: "敏捷", 41: "智识", 42: "意志"}

# 技能槽位 -> SkillData 文件名后缀（顺序即普攻链顺序）
_SUFFIX_ORDER = [
    ("attack1", "normal_attack", 1),
    ("attack2", "normal_attack", 2),
    ("attack3", "normal_attack", 3),
    ("attack4", "normal_attack", 4),
    ("attack5", "normal_attack", 5),
    ("power_attack", "power_attack", 0),
    ("normal_skill", "battle_skill", 0),
    ("combo_skill", "combo_skill", 0),
    ("ultimate_skill", "ultimate", 0),
    ("dash_attack", "dash_attack", 0),
    ("plunging_attack_end", "plunging_attack", 0),
]


# ---------------------------------------------------------------- 工具

def fetch_skills(src: "github.GithubSource", skill_ids: list[str],
                 workers: int = 8) -> dict[str, str]:
    """并行预取技能文件；返回 {skill_id: status}。失败不抛，只记录。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    status: dict[str, str] = {}

    def one(sid: str) -> tuple[str, str]:
        p = src.local_path(f"Json/SkillData/{sid}.json")
        if p.exists() and p.stat().st_size > 0:
            return sid, "cached"
        try:
            got = src.skill(sid)
            return sid, "ok" if got is not None else "missing"
        except Exception as e:  # noqa: BLE001
            return sid, f"err:{type(e).__name__}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, sid) for sid in skill_ids]
        for f in as_completed(futs):
            sid, st = f.result()
            status[sid] = st
    return status

def load_i18n(src: github.GithubSource) -> dict:
    raw = src.table("I18nTextTable_CN.json")
    return raw if isinstance(raw, dict) else {}


def _text(node) -> str | None:
    if isinstance(node, dict):
        tid = node.get("id")
        if tid is None:
            return None
        txt = node.get("text") or ""
        if txt:
            return txt
        return None
    return None


def _hi(it: int | None) -> float:
    lv = max(1, min(int(it or 1), 90))
    return 1.0 + (lv - 1) / 196.0


def _class_name(node) -> str:
    """'Beyond.Gameplay.Core.DamageAction+DamageActionData, Gameplay.Beyond'
    -> 'DamageAction'"""
    if not isinstance(node, str):
        return ""
    head = node.split(",")[0]
    head = head.split(".")[-1]
    return head.split("+")[0]


def _walk(node, offset: int, hits: list) -> None:
    if isinstance(node, list):
        for x in node:
            _walk(x, offset, hits)
        return
    if not isinstance(node, dict):
        return
    off = offset
    sf = node.get("_startFrame")
    if isinstance(sf, (int, float)):
        off = offset + int(sf)
    if _class_name(node.get("$type")) == "DamageAction":
        for unit in node.get("damageUnits") or []:
            dt = unit.get("damageAttributeType", 0)
            calc = (unit.get("atkCalculation") or {}).get("atkScale") or {}
            key = None
            if calc.get("useBlackboardKey") and calc.get("blackboardKey"):
                key = calc["blackboardKey"]
            else:
                alt = unit.get("atkScale") or {}
                if alt.get("useBlackboardKey") and alt.get("blackboardKey"):
                    key = alt["blackboardKey"]
            hits.append({"frame": off, "key": key, "damage_type": dt})
        return
    for k, v in node.items():
        if k in ("_position", "$type", "skillHighlightCondition"):
            continue
        _walk(v, off, hits)


def _hits(skill: dict) -> list[dict]:
    """递归扫 timelineActions，收集所有伤害实例。"""
    ag = (skill or {}).get("actionGroupData") or {}
    hits: list[dict] = []
    for tl in ag.get("timelineActions") or []:
        _walk(tl, 0, hits)
    # 去重（同一分支可能被多次记录），但保留不同帧
    seen = set()
    out = []
    for h in hits:
        k = (h["frame"], h.get("key"), h["damage_type"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    out.sort(key=lambda h: (h["frame"], h["damage_type"] or 0))
    return out


def _parse_template(wikitext: str) -> dict[str, str]:
    """从 {{干员|k=v|...}} 里抽 k=v（值可跨行，遇到下一个 |k= 结束）。"""
    if not wikitext:
        return {}
    body = wikitext
    if "{{干员" in body:
        body = body.split("{{干员", 1)[1]
    fields: dict[str, str] = {}
    # 以「换行 + 可选空白 + |key=」为分隔
    parts = re.split(r"\n\s*\|(?=[\u4e00-\u9fffA-Za-z0-9_·]+\s*=)", body)
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_·]+", k):
            continue
        fields[k] = v.strip()
    return fields


def _clean(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\{\{颜色\|[^|}]+\|([^}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{异常\|([^}]*)\}\}", r"\1", s)
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.strip()
    return s or None


# ---------------------------------------------------------------- 主流程

def build(force: bool = False, verbose: bool = True) -> dict:
    config.ensure_dirs()
    src = github.GithubSource()
    head = src.head(refresh=force)

    if verbose:
        print(f"[build] 数据源 {head['repo']}@{head.get('short_sha')} ({head.get('date')})")

    chars = src.table("CharacterTable.json")
    patches = src.table("SkillPatchTable.json")
    types = src.table("CharTypeTable.json")
    profs = src.table("CharProfessionTable.json")
    tags_tbl = src.table("CharBattleTagTable.json")
    battle_const = src.table("BattleConst.json")
    i18n = load_i18n(src)
    weapons: dict = {}
    weapon_curves: dict = {}
    try:
        weapons = src.table("WeaponBasicTable.json") or {}
        weapon_curves = src.table("WeaponUpgradeTemplateTable.json") or {}
        if verbose:
            print(f"[build] 武器 {len(weapons)} 把 / 成长曲线 {len(weapon_curves)} 条")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"[build] 武器表不可用：{type(e).__name__}")

    # 天赋 / 潜能 数值表
    teffects: dict = {}
    try:
        teffects = src.table("PotentialTalentEffectTable.json") or {}
        if verbose:
            print(f"[build] 天赋/潜能效果条目 {len(teffects)}")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"[build] 天赋表不可用：{type(e).__name__}")
    _DTYPE_ATTRS = {17, 28, 29, 32, 33, 50, 51, 52, 53, 54, 55, 61, 64, 65, 66, 67, 68, 69}

    def t(node) -> str | None:
        direct = _text(node)
        if direct:
            return direct
        if isinstance(node, dict) and node.get("id") is not None:
            return i18n.get(str(node["id"]))
        return None

    # ---- wiki 索引（允许失败）
    wiki_index: dict[str, dict] = {}
    wiki_meta: dict = {}
    try:
        for row in wiki.operator_index(force=force):
            wiki_index[row["name"]] = row
        wiki_meta = wiki.site_meta(force=force)
        if verbose:
            print(f"[build] wiki 干员索引 {len(wiki_index)} 条；{wiki_meta.get('generator')}")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"[build] wiki 不可用，降级：{type(e).__name__}: {e}")

    # ---- 技能补丁索引：只保留干员技能
    patch_by_id = patches

    # ---- 预热：并行拉取所有需要的技能文件（受控并发，避免打爆 CDN）
    needed: list[str] = []
    for cid in chars:
        if not cid.startswith("chr_"):
            continue
        for suffix, _slot, _stage in _SUFFIX_ORDER:
            skid = f"{cid}_{suffix}"
            if skid in patch_by_id:
                needed.append(skid)
    if verbose:
        print(f"[build] 需要 {len(needed)} 个技能文件（已缓存则跳过）")
    fetch_skills(src, needed, workers=8)

    out_dir = config.NORM / "operators"
    out_dir.mkdir(parents=True, exist_ok=True)

    operators: dict[str, Operator] = {}
    for cid, ch in sorted(chars.items()):
        if not cid.startswith("chr_"):
            continue
        name = t(ch.get("name")) or ch.get("engName") or cid
        elem = config.ELEMENT_ZH.get(ch.get("charTypeId"), ch.get("charTypeId"))
        prof_id = ch.get("profession")
        prof_node = (profs.get(str(prof_id)) or {}).get("name")
        profession = t(prof_node) or config.PROFESSION_FALLBACK.get(prof_id, str(prof_id))
        weapon_id = ch.get("defaultWeaponId") or ""
        weapon_type = None
        for key, zh in _WEAPON_NAME.items():
            if key in weapon_id:
                weapon_type = zh
                break
        wk = wiki_index.get(name)

        # 以 wiki 数据校正（wiki 更贴近中文口径）
        if wk:
            profession = wk.get("profession") or profession
            elem = wk.get("element") or elem
            weapon_type = wk.get("weapon_type") or weapon_type

        # ---- 属性成长
        stats: list[dict] = []
        seen_levels: set[int] = set()
        for entry in ch.get("attributes") or []:
            attrs = ((entry or {}).get("Attribute") or {}).get("attrs") or []
            row: dict = {"break_stage": entry.get("breakStage", 0)}
            for a in attrs:
                f = _STAT_IDS.get(a.get("attrType"))
                if f:
                    row[f] = a.get("attrValue")
            lv = int(row.get("level") or len(stats) + 1)
            row["level"] = lv
            if lv in seen_levels:
                # 同等级多份（突破前后）：保留突破更高的一份
                for i, prev in enumerate(stats):
                    if prev["level"] == lv and (prev.get("break_stage") or 0) <= row["break_stage"]:
                        stats[i] = row
                        break
                continue
            seen_levels.add(lv)
            stats.append(row)
        stats.sort(key=lambda r: r["level"])

        # ---- 技能
        skills: dict[str, Skill] = {}
        chain: list[tuple[int, str]] = []
        for suffix, slot, stage in _SUFFIX_ORDER:
            skid = f"{cid}_{suffix}"
            patch = patch_by_id.get(skid)
            if not patch:
                continue
            rows = patch.get("SkillPatchDataBundle") or []
            if not rows:
                continue
            raw_skill = src.skill(skid, force=force)
            raw_hits = _hits(raw_skill or {})
            hp_hits = [h for h in raw_hits if h["damage_type"] == 0]
            estimated = False
            if not hp_hits:
                mult = (rows[0].get("blackboard") or [])
                if any(float(x.get("value") or 0) != 0 for x in mult):
                    cand = (raw_skill or {}).get("exclusiveFrame") or 10
                    hp_hits = [{"frame": int(cand), "key": "atk_scale",
                                "damage_type": 0}]
                    estimated = True
            all_hits = [h for h in raw_hits if h["damage_type"] != 0] + hp_hits
            all_hits.sort(key=lambda h: (h["frame"], h["damage_type"] or 0))
            timing = Timing(
                duration_frames=(raw_skill or {}).get("durationFrame"),
                exclusive_frames=(raw_skill or {}).get("exclusiveFrame"),
                offset_frames=(raw_skill or {}).get("offsetRecordFrame"),
                hits=[Hit(frame=int(h["frame"]), key=h.get("key"),
                          damage_type=h["damage_type"]) for h in all_hits],
                estimated=estimated,
                source="Json/SkillData" if raw_skill else "missing",
            )
            levels: list[SkillLevel] = []
            for r in rows:
                bb = {x["key"]: float(x.get("value") or 0.0)
                      for x in (r.get("blackboard") or [])}
                cost_type = r.get("costType")
                cost_value = r.get("costValue")
                levels.append(SkillLevel(
                    level=int(r.get("level") or len(levels) + 1),
                    multipliers=bb,
                    sp_cost=cost_value if cost_type == 1 else None,
                    cooldown_s=r.get("coolDown"),
                    ult_cost=cost_value if cost_type == 0 and slot == "ultimate" else None,
                    energy_gain=bb.get("usp"),
                ))
            bb0 = {x["key"]: float(x.get("value") or 0.0)
                   for x in (rows[0].get("blackboard") or [])}
            has_dmg = any(v > 0 for k, v in bb0.items()
                          if k.startswith("atk_scale"))
            has_heal = any(v > 0 for k, v in bb0.items()
                           if k.startswith("heal"))
            role = ("damage" if has_dmg
                    else "heal" if has_heal else "buff")
            key = skid if slot != "normal_attack" else skid
            skills[key] = Skill(
                skill_id=skid, slot=slot, stage=stage,
                element=None,  # 由 wiki 的「战技属性」等字段补齐
                timing=timing, levels=levels,
                name_zh=None, role=role,
                sources={"multipliers": "SkillPatchTable", "timing": "Json/SkillData"},
            )
            if slot == "normal_attack":
                chain.append((stage, skid))
        chain.sort()

        op = Operator(
            char_id=cid, name_zh=name, eng_name=ch.get("engName"),
            rarity=ch.get("rarity"), profession=profession, element=elem,
            weapon_type=weapon_type,
            main_attr=_ATTR_ZH.get(ch.get("mainAttrType")),
            sub_attr=_ATTR_ZH.get(ch.get("subAttrType")),
            default_weapon_id=weapon_id,
            battle_tags=[t(tags_tbl.get(x)) or x for x in (ch.get("charBattleTagIds") or [])],
            stats=stats, skills=skills,
            normal_chain=[s for _, s in chain],
            sources={
                "stats": "CharacterTable",
                "skills": "SkillPatchTable",
                "timing": "Json/SkillData",
                "labels": "wiki SMW" if wk else "CharacterTable/i18n",
            },
        )
        w = weapons.get(weapon_id) or {}
        curve = (weapon_curves.get(w.get("levelTemplateId") or "") or {}).get("list") or []
        wk_bb = [{} for _ in range(9)]
        for wsid in (w.get("weaponSkillList") or []):
            if not (wsid.startswith("wpn_attr_") or wsid.startswith("wpn_sp_attr_")):
                continue
            wrows = patch_by_id.get(wsid) or {}
            for row in wrows.get("SkillPatchDataBundle") or []:
                lv = int(row.get("level") or 1)
                slot = wk_bb[min(lv, 9) - 1]
                for x in row.get("blackboard") or []:
                    v = float(x.get("value") or 0.0)
                    if v:
                        slot[x["key"]] = slot.get(x["key"], 0.0) + v
        op.weapon = {
            "skill_bb": wk_bb,
            "id": weapon_id,
            "rarity": w.get("rarity"),
            "max_lv": w.get("maxLv"),
            "atk_by_level": [int(x.get("baseAtk") or 0) for x in curve],
            "skills": w.get("weaponSkillList") or [],
        }
        op.mods = _collect_mods(cid, teffects, t)
        operators[cid] = op

    # ---- wiki 文本（逐干员，限速）
    wsd: dict = {}
    try:
        wsd = wiki.skilldata(force=force) or {}
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"[build] wiki skilldata 不可用：{type(e).__name__}")

    wiki_ok = 0
    for cid, op in operators.items():
        if not op.name_zh:
            continue
        try:
            wt = wiki.page_wikitext(op.name_zh, force=force)
            if not wt:
                continue
            f = _parse_template(wt)
            op.wiki = {
                "title": op.name_zh,
                "revid": wiki.page_revid(op.name_zh),
                "raw_len": len(wt),
                "fields": {k: _clean(v) for k, v in f.items()
                           if k in _KEEP_WIKI_FIELDS and _clean(v)},
            }
            # 用 wiki 的技能中文名/描述补进 skills
            _apply_wiki_skill_text(op, f)
            _attach_anchors(op, f, wsd.get(op.name_zh))
            wiki_ok += 1
            wiki.polite_sleep()
        except Exception as e:  # noqa: BLE001
            op.wiki = {"title": op.name_zh, "error": f"{type(e).__name__}: {e}"}

    # ---- 落盘
    for cid, op in operators.items():
        (out_dir / f"{cid}.json").write_text(
            json.dumps(op.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    version = _dataset_version(head, operators, wiki_meta)
    coverage = compute_coverage(operators, wiki_index)
    cross: dict = {}
    try:
        if wsd:
            cross = cov_crosscheck(operators, wsd)
    except Exception as e:  # noqa: BLE001
        cross = {"error": f"{type(e).__name__}: {e}"}
    dataset = {
        "version": version,
        "schema_version": config.SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "operator_ids": sorted(operators),
        "sources": {
            "github": head,
            "wiki": wiki_meta,
            "tables": ["CharacterTable", "SkillPatchTable", "BattleConst",
                       "AttributeMetaTable", "CharTypeTable", "CharProfessionTable",
                       "CharBattleTagTable", "I18nTextTable_CN"],
        },
        "constants": {"battle_const": battle_const,
                      "attr_names": {str(k): v for k, v in config.ATTR_NAMES.items()}},
        "coverage": coverage,
        "crosscheck": {k: v for k, v in cross.items() if k != "details"} if cross else {},
    }
    (config.NORM / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 装备索引（属性向量 + 套装）
    try:
        eq = src.table("EquipTable.json") or {}
        suits = src.table("EquipSuitTable.json") or {}
        equip_index = {}
        for iid, row in eq.items():
            attrs = {}
            for m in row.get("equipAttrModifiers") or []:
                vals = m.get("attrValues") or []
                if not vals:
                    continue
                key = str(m.get("attrType"))
                attrs[key] = [float(v or 0.0) for v in vals[:4]]
            equip_index[iid] = {"attrs": attrs}
        suit_index = {sid: {"pieces": (v.get("equipList") or [])[:12]}
                      for sid, v in suits.items()}
        (config.NORM / "equip.json").write_text(
            json.dumps({"items": equip_index, "suits": suit_index},
                       ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"[build] 装备索引跳过：{type(e).__name__}")

    manifest = {
        "version": version,
        "built_at": dataset["created_at"],
        "github": head,
        "wiki": wiki_meta,
        "operators": len(operators),
        "operators_with_wiki": wiki_ok,
        "coverage": coverage,
        "crosscheck": {k: v for k, v in cross.items() if k != "details"} if cross else {},
    }
    (config.NORM / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    if cross and cross.get("matched"):
        from .coverage import write_crosscheck
        write_crosscheck(cross)

    if verbose:
        print(f"[build] {len(operators)} 干员，{wiki_ok} 有 wiki 文本，version={version}")
    return manifest


def _collect_mods(cid: str, teffects: dict, tr) -> dict:
    """把天赋/潜能效果整理成 {talents: {level: [entry]}, potentials: {n: [entry]}}。"""
    import re as _re
    talents: dict[int, dict[int, list]] = {}
    potentials: dict[int, list] = {}
    for key, row in (teffects or {}).items():
        m = _re.match(rf"{_re.escape(cid)}_talent_(\d+)_(\d+)$", key)
        mp = _re.match(rf"{_re.escape(cid)}_potential_(\d+)$", key)
        if not m and not mp:
            continue
        entries = []
        for d in row.get("dataList") or []:
            am = d.get("attrModifier") or {}
            sb = d.get("skillBbModifier") or {}
            cond = bool(d.get("activeCondition"))
            if am.get("attrValue"):
                entries.append({"kind": "attr", "attr": am.get("attrType"),
                                "value": am.get("attrValue"), "conditional": cond})
            if sb.get("bbKey") and sb.get("floatValue") not in (None, 0.0, 1.0):
                entries.append({"kind": "bb", "key": sb.get("bbKey"),
                                "value": sb.get("floatValue"),
                                "skill_id": sb.get("skillId") or "",
                                "conditional": cond})
        if not entries:
            continue
        if m:
            tal = int(m.group(1)); lv = int(m.group(2))
            talents.setdefault(tal, {})[lv] = entries
        else:
            potentials[int(mp.group(1))] = entries
    return {"talents": {str(k): {str(l): v for l, v in d.items()}
                        for k, d in talents.items()},
            "potentials": {str(k): v for k, v in potentials.items()}}


# ---------------------------------------------------------------- wiki 文本

_KEEP_WIKI_FIELDS = {
    "干员代号", "稀有度", "职业", "属性", "武器", "TAG", "阵营", "种族",
    "主属性", "副属性", "实装日期", "获取途径",
    "普攻名称", "普通攻击描述", "下落攻击描述", "处决攻击描述",
    "战技名称", "战技描述", "连携技名称", "连携技描述",
    "终结技名称", "终结技描述",
    "天赋1", "天赋1阶段1", "天赋1阶段2", "天赋1阶段3",
    "天赋2", "天赋2阶段1", "天赋2阶段2", "天赋2阶段3",
    "潜能1", "潜能1描述", "潜能2", "潜能2描述", "潜能3", "潜能3描述",
    "潜能4", "潜能4描述", "潜能5", "潜能5描述",
}
_KEEP_WIKI_FIELDS |= {f"{slot}效果{i}标题"
                      for slot in ("战技", "连携技", "终结技")
                      for i in range(1, 9)}

_FIELD_TO_SKILL = {
    "战技名称": "battle_skill", "战技描述": "battle_skill",
    "连携技名称": "combo_skill", "连携技描述": "combo_skill",
    "终结技名称": "ultimate", "终结技描述": "ultimate",
    "普攻名称": "normal_attack", "普通攻击描述": "normal_attack",
}


_CN_NUM = "一二三四五"


def _attach_anchors(op: Operator, fields: dict[str, str], w: dict | None) -> None:
    """把 wiki 的"展示口径"总倍率挂到技能上，作为伤害总量的锚点。

    为什么需要：客户端表里同一段普攻常有多个 DamageAction 实例
    （命中判定 + 展示用），直接求和会和游戏里看到的倍率对不上。
    wiki 的数字是人工核对的整段值，更适合当"总量"，帧数据只负责分配时间。
    """
    if not w:
        return
    for i, sid in enumerate(op.normal_chain, start=1):
        if i > 5:
            break
        arr = w.get(f"普攻第{_CN_NUM[i - 1]}段倍率")
        sk = op.skills.get(sid)
        if arr and sk:
            sk.wiki_anchor = {"field": f"普攻第{i}段倍率", "levels": arr,
                              "title": "伤害倍率", "source": "wiki Skilldata"}
    for slot, zh in (("battle_skill", "战技"), ("combo_skill", "连携技"),
                     ("ultimate", "终结技")):
        title = (fields.get(f"{zh}效果1标题") or "").strip()
        if not title or ("伤害" not in title and "倍率" not in title):
            continue
        arr = w.get(f"{zh}效果1")
        if not arr:
            continue
        sk = next((s for s in op.skills.values() if s.slot == slot), None)
        if sk:
            sk.wiki_anchor = {"field": f"{zh}效果1", "levels": arr,
                              "title": title, "source": "wiki Skilldata"}


def _apply_wiki_skill_text(op: Operator, f: dict[str, str]) -> None:
    def slot_skill(slot: str) -> Skill | None:
        for sk in op.skills.values():
            if sk.slot == slot:
                return sk
        return None

    for field, slot in _FIELD_TO_SKILL.items():
        val = _clean(f.get(field))
        if not val:
            continue
        target = slot_skill(slot)
        if not target:
            continue
        if field.endswith("名称"):
            target.name_zh = val
        else:
            target.tags = target.tags
            target.semantics.setdefault("text_zh", val)
    # 元素：战技/连携/终结技属性
    for field, slot in (("战技属性", "battle_skill"), ("连携技属性", "combo_skill"),
                        ("终结技属性", "ultimate"), ("普攻属性", "normal_attack")):
        val = _clean(f.get(field))
        if not val:
            continue
        for sk in op.skills.values():
            if sk.slot == slot:
                sk.element = val
    # 天赋/潜能
    op.talents = [
        {"name": _clean(f.get("天赋1")), "desc": _clean(f.get("天赋1阶段2") or f.get("天赋1阶段1"))},
        {"name": _clean(f.get("天赋2")), "desc": _clean(f.get("天赋2阶段2") or f.get("天赋2阶段1"))},
    ]
    op.potentials = [
        {"index": i, "name": _clean(f.get(f"潜能{i}")), "desc": _clean(f.get(f"潜能{i}描述"))}
        for i in range(1, 6)
    ]


# ---------------------------------------------------------------- 版本/覆盖

def _dataset_version(head: dict, ops: dict, wiki_meta: dict) -> str:
    h = hashlib.sha256()
    h.update(f"schema={config.SCHEMA_VERSION}".encode())
    h.update(str(head.get("sha")).encode())
    h.update(str(wiki_meta.get("articles")).encode())
    for cid in sorted(ops):
        op = ops[cid]
        h.update(cid.encode())
        h.update(json.dumps(op.wiki.get("fields") or {}, ensure_ascii=False,
                            sort_keys=True).encode())
        for sk in op.skills.values():
            h.update(sk.skill_id.encode())
            h.update(str(sk.timing.to_dict()).encode())
            h.update(str(len(sk.levels)).encode())
    return h.hexdigest()[:12]


def compute_coverage(operators: dict[str, Operator], wiki_index: dict) -> dict:
    total = len(operators)
    with_timing = 0
    with_mults = 0
    with_wiki = 0
    missing_chain: list[str] = []
    missing_power: list[str] = []
    missing_battle: list[str] = []
    for cid, op in operators.items():
        if all(sk.timing.duration_frames is not None for sk in op.skills.values()):
            with_timing += 1
        if all(sk.levels for sk in op.skills.values()):
            with_mults += 1
        if op.wiki.get("fields"):
            with_wiki += 1
        if len(op.normal_chain) < 3:
            missing_chain.append(cid)
        if not any(s.slot == "power_attack" for s in op.skills.values()):
            missing_power.append(cid)
        if not any(s.slot == "battle_skill" for s in op.skills.values()):
            missing_battle.append(cid)
    return {
        "operators": total,
        "with_full_timing": with_timing,
        "with_multipliers": with_mults,
        "with_wiki_text": with_wiki,
        "missing_normal_chain": missing_chain,
        "missing_power_attack": missing_power,
        "missing_battle_skill": missing_battle,
        "wiki_only": sorted(set(wiki_index) - {o.name_zh for o in operators.values()}),
    }
