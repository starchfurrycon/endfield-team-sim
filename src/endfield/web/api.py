"""API 逻辑层（不碰 HTTP）。

server.py 只做路由；这里的函数可以直接被单测/CLI 调用。
"""

from __future__ import annotations

from .. import __version__, config
from ..engine.battle import Scenario
from ..model import Dataset
from ..search.rotations import search_rotation
from ..search.teams import TeamQuery, recommend
from . import jobs
from .state import STATE


# ---------------------------------------------------------------- 状态

def status() -> dict:
    snap = STATE.snapshot()
    data = dict(snap["data"])
    data["schema_version"] = snap["schema_version"]
    data["llm_run"] = snap["llm"]
    data["events"] = snap["events"]
    return data


# ---------------------------------------------------------------- 基础数据

def meta() -> dict:
    ds = dataset()
    from ..data import curated as curated_mod
    # 同一中文名可能有多份客户端数据（管理员 男/女/占位），UI 只该看到一个人
    groups: dict[str, list] = {}
    for op in ds.operators.values():
        if not op.name_zh:
            continue
        if not any(s.slot == "battle_skill" for s in op.skills.values()):
            continue
        groups.setdefault(op.name_zh, []).append(op)

    def rank(o) -> tuple:
        return (len(o.skills), o.rarity or 0, o.char_id)

    picked = [(name, max(ops, key=rank)) for name, ops in groups.items()]
    picked.sort(key=lambda x: (x[1].rarity or 0, x[0]), reverse=True)

    ops = []
    for name, op in picked:
        ops.append({
            "id": op.char_id,
            "name": op.name_zh,
            "eng_name": op.eng_name,
            "rarity": op.rarity,
            "profession": op.profession,
            "element": op.element,
            "weapon": op.weapon_type,
            "tags": op.battle_tags,
            "main_attr": op.main_attr,
            "combo_trigger": (op.semantics or {}).get("combo_trigger"),
            "hit_dependent": (op.semantics or {}).get("has_hit_dependent_skill"),
            "avatar": (config.AVATARS / f"{op.char_id}.png").is_file(),
            "variants": sorted(o.char_id for o in groups[name]),
            "note": (curated_mod.spec_for(op.char_id) or {}).get("note"),
        })

    llm_info = {}
    try:
        llm, llm_config = _llm_mod()
        cfg = llm_config.load()
        llm_info = {"available": llm_config.available(cfg),
                    "model": cfg.get("model"),
                    "prompt_version": llm.PROMPT_VERSION,
                    "cache_files": llm.cache_stats()["files"]}
    except Exception:  # noqa: BLE001
        pass

    return {"version": ds.version, "count": len(ops), "operators": ops,
            "app_version": __version__,
            "llm": llm_info,
            "constants": {k: v for k, v in config.GUESSED.items()},
            "enemies": {"slow": 12, "normal": 8, "fast": 5, "burst": 4},
            "equip_presets": [{"id": "none", "label": "不穿装备"},
                              {"id": "output", "label": "输出向套装"},
                              {"id": "sustain", "label": "功能向套装"}]}


def dataset() -> Dataset:
    return STATE.dataset()


# ---------------------------------------------------------------- 战斗

def _scenario(ds: Dataset, ids: list[str], payload: dict) -> Scenario:
    sc = Scenario(
        team=ids[:4],
        start_energy=str(payload.get("mode") or "cold"),
        duration_s=float(payload.get("duration", 40)),
        level=int(payload.get("level", 90)),
        skill_level=int(payload.get("skill_level", 12)),
        enemy_profile=str(payload.get("enemy_profile")
                          or payload.get("enemy") or "normal"),
    )
    from ..search import loadout as lo
    lo.apply_presets(ds, sc,
                     equip=payload.get("equip") or "none",
                     weapon=int(payload.get("weapon") or 0),
                     equip_level=int(payload.get("equip_level", 3)))
    return sc


def axis(payload: dict) -> dict:
    ds = dataset()
    ids = payload.get("team") or []
    if not ids:
        raise ValueError("team 为空")
    sc = _scenario(ds, ids, payload)
    rr = search_rotation(ds, sc, beam=int(payload.get("beam", 3)),
                         steps=int(payload.get("steps", 26)),
                         opening_ults=(sc.start_energy == "hot"))
    return {"mode": sc.start_energy, "rotation": rr.to_dict(),
            "config": _config_summary(ds, sc)}


def _config_summary(ds: Dataset, sc: Scenario) -> dict:
    names = {cid: (ds.operators[cid].name_zh or cid) for cid in sc.team
             if cid in ds.operators}
    equip = {names.get(cid, cid): list(ids)
             for cid, ids in (sc.equip_by_op or {}).items()}
    weapons = {names.get(cid, cid): wid
               for cid, wid in (sc.weapon_by_op or {}).items()}
    return {
        "mode": sc.start_energy,
        "mode_label": "热启动" if sc.start_energy == "hot" else "冷启动",
        "enemy_profile": sc.enemy_profile,
        "duration_s": sc.duration_s,
        "level": sc.level,
        "skill_level": sc.skill_level,
        "equip_level": sc.equip_level,
        "equip_by_op": equip,
        "weapon_by_op": weapons,
        "notes": [f"{'热' if sc.start_energy == 'hot' else '冷'}启动",
                  f"敌人节奏 {sc.enemy_profile}",
                  f"{sc.duration_s:g}s 窗口"]
                 + ([f"装备 {len(equip)} 人"] if equip else ["无装备"])
                 + ([f"换武器 {len(weapons)} 人"] if weapons else []),
    }


def compare(payload: dict) -> dict:
    """两条方案并排对比（同队、不同启动方式/敌人节奏/装备/武器）。"""
    ds = dataset()
    ids = [x for x in (payload.get("team") or []) if x]
    if not ids:
        raise ValueError("team 为空")
    base = {k: payload[k] for k in ("duration", "level", "skill_level") if k in payload}
    beam = int(payload.get("beam", 3))
    steps = int(payload.get("steps", 26))
    names = {cid: (ds.operators[cid].name_zh or cid) for cid in ids[:4]
             if cid in ds.operators}

    out: dict = {"team": ids[:4], "name_map": names, "sides": {}}
    for side in ("a", "b"):
        spec = dict(base)
        spec.update(payload.get(side) or {})
        sc = _scenario(ds, ids, spec)
        rr = search_rotation(ds, sc, beam=beam, steps=steps,
                             opening_ults=(sc.start_energy == "hot"))
        cfg = _config_summary(ds, sc)
        out["sides"][side] = {
            "side": side.upper(),
            "label": " · ".join(cfg["notes"][:1] + cfg["notes"][2:]) or side.upper(),
            "config": cfg,
            "rotation": rr.to_dict(),
        }

    a = out["sides"]["a"]["rotation"]["result"]
    b = out["sides"]["b"]["rotation"]["result"]
    da, db = float(a["total_damage"]), float(b["total_damage"])
    base_dmg = max(1.0, da)
    out["delta"] = {
        "total_damage": round(db - da, 1),
        "dps": round(float(b["dps"]) - float(a["dps"]), 2),
        "damage_pct": round((db - da) / base_dmg * 100.0, 2),
        "duration_frames": int(b["duration_frames"]) - int(a["duration_frames"]),
    }
    return out


def team(payload: dict) -> dict:
    ds = dataset()
    q = TeamQuery(
        must=payload.get("must") or [],
        ban=payload.get("ban") or [],
        pool=payload.get("pool") or [],
        size=int(payload.get("size", 4)),
        top_k=int(payload.get("top", 3)),
        evaluate=int(payload.get("evaluate", 8)),
        duration_s=float(payload.get("duration", 40)),
    )
    res = recommend(ds, q, level=int(payload.get("level", 90)),
                    skill_level=int(payload.get("skill_level", 12)), verbose=False)
    names = {op.char_id: op.name_zh for op in ds.operators.values()}
    for t in res["top"]:
        t["name_map"] = {cid: names.get(cid, cid) for cid in t["team"]}
    return res


def loadout(payload: dict) -> dict:
    from ..search import loadout as lo
    ds = dataset()
    ids = [x for x in (payload.get("team") or []) if x]
    if not ids:
        raise ValueError("team 为空")
    return {"team": ids,
            "recommendations": lo.recommend_for_team(
                ds, ids[:4],
                int(payload.get("weapons", 3)),
                int(payload.get("sets", 2)))}


# ---------------------------------------------------------------- LLM

def _llm_mod():
    from ..semantics import llm, llm_config
    return llm, llm_config


def llm_status() -> dict:
    llm, llm_config = _llm_mod()
    cfg = llm_config.load()
    st = llm.cache_stats()
    return {**llm_config.public(cfg),
            "available": llm_config.available(cfg),
            "prompt_version": llm.PROMPT_VERSION,
            "cache_files": st["files"],
            "cache_kb": round(st["bytes"] / 1024),
            "run": STATE.snapshot()["llm"]}


def llm_save(payload: dict) -> dict:
    _, llm_config = _llm_mod()
    patch = {k: payload[k] for k in
             ("enabled", "base", "model", "key", "temperature", "max_tokens",
              "timeout_s", "price_in", "price_out") if k in payload}
    cfg = llm_config.save(patch)          # 校验失败抛 ValueError
    return {**llm_status(), "saved": True,
            "key_masked": llm_config.mask_key(cfg["key"])}


def llm_test() -> dict:
    llm, _ = _llm_mod()
    return llm.test_connection()


def start_llm_run(mode: str = "gaps", limit: int | None = None) -> dict:
    return jobs.start_llm(mode=mode, limit=limit)
