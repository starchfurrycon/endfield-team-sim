"""把一条轴翻译成"怎么打"：按时间的动作步骤 + 最多 3 条要点。

原则：
  * 只讲轴上真实发生的事（时间、谁、什么技能、为什么能放）；
  * 不发明"切人/主控"这类引擎没建模的操作，只用承诺帧说明"放技能时别切走"；
  * 步骤只列战技/连携/终结技，普攻与重击归为"填充"，避免变成文字墙。
"""

from __future__ import annotations

from ..engine.battle import Result, Scenario
from ..model import Dataset

SLOT_LABEL = {
    "battle_skill": "战技",
    "combo_skill": "连携",
    "ultimate": "终结技",
    "power_attack": "重击",
    "normal_attack": "普攻",
}
TRIGGER_LABEL = {
    "on_break": "破防后",
    "on_status": "挂上状态后",
    "on_hit_taken": "受击后",
    "on_skill": "技能后",
    "on_ult": "终结技后",
    "on_kill": "击杀后",
    "on_stagger": "失衡后",
    "on_low_hp": "残血时",
    "on_combo": "连携后",
}
MAX_STEPS = 14


def _skill_of(op, slot: str):
    for sk in op.skills.values():
        if sk.slot == slot:
            return sk
    return None


def _merge(casts: list[dict]) -> list[dict]:
    """相邻的同人同技能合并成一步（显示 ×N）。"""
    out: list[dict] = []
    for c in casts:
        last = out[-1] if out else None
        if last and last["cid"] == c["cid"] and last["slot"] == c["slot"] \
                and c["t"] - last["t_end"] <= 2.2:
            last["count"] += 1
            last["t_end"] = c["t_end"]
        else:
            out.append(dict(c))
    return out


def build(ds: Dataset, sc: Scenario, res: Result) -> dict:
    names = {cid: (ds.operators[cid].name_zh or cid)
             for cid in sc.team if cid in ds.operators}
    casts: list[dict] = []
    for ev in res.timeline:
        if ev.get("kind") != "cast":
            continue
        slot = ev.get("slot") or ""
        cid = ev.get("op") or ""
        if slot not in ("battle_skill", "combo_skill", "ultimate") or not cid:
            continue
        op = ds.operators.get(cid)
        sk = _skill_of(op, slot) if op else None
        sem = (sk.semantics if sk else None) or {}
        trig = TRIGGER_LABEL.get(sem.get("trigger") or "", "")
        note = ""
        if slot == "combo_skill" and trig:
            note = trig
        elif slot == "ultimate":
            note = "能量满"
        elif slot == "battle_skill" and not casts:
            note = "起手"
        casts.append({
            "cid": cid,
            "name": names.get(cid, cid),
            "slot": slot,
            "label": SLOT_LABEL.get(slot, slot),
            "t": round(ev["frame"] / 30.0, 2),
            "t_end": round((ev.get("commit_end") or ev["frame"]) / 30.0, 2),
            "note": note,
            "count": 1,
            "skill": (sk.name_zh if sk else ""),
        })

    steps = _merge(casts)[:MAX_STEPS]

    # 连携触发条件（去重后按人分组）
    combos: dict[str, list[str]] = {}
    for c in casts:
        if c["slot"] != "combo_skill" or not c["note"]:
            continue
        combos.setdefault(c["note"], [])
        if c["name"] not in combos[c["note"]]:
            combos[c["note"]].append(c["name"])

    tips: list[str] = []
    if steps:
        head = steps[:3]
        seq = " → ".join(f"{s['label']}" for s in head)
        first = head[0]
        tips.append(f"开场 {first['t']:.1f}s 起手：{seq}"
                    + (f"（{first['name']}先动）" if first["name"] else ""))
    if combos:
        parts = [f"{'/'.join(v)} {k}" for k, v in combos.items()]
        tips.append("连携别空放：" + "；".join(parts))
    caps = (res.stats or {}).get("energy_cap") or {}
    if caps:
        nums = {cid: int(round(v)) for cid, v in caps.items() if cid in names}
        if nums:
            tips.append("终结技攒满再放：" + " / ".join(
                f"{names[cid]} {n}" for cid, n in nums.items()))
    if not tips:
        tips.append("这条轴没有技能施放：检查技力/能量是否足够。")

    return {
        "steps": steps,
        "tips": tips[:3],
        "casts": (res.stats or {}).get("cast_counts") or {},
        "note": "放技能时别切走：承诺帧内换人会丢掉这次伤害。",
    }
