"""从客户端表里抽取「机制原文 + 数值」，生成 mechanics 报告。

为什么单独做一层：机制数值散落在三类地方，
  1. 游戏内教学页（WikiTutorialPageTable，文本 id 要用 I18n 解）
  2. 战斗规则 buff（buff_dung_*，黑板里是数值）
  3. 技能/属性表（已有）
把它们对齐后写成人可读的报告，避免再出现"以为数据里没有"的判断错误。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

TABLES = config.RAW / "github" / "TableCfg"
BUFFS = config.RAW / "github" / "Json" / "BuffData"
REPORTS = config.REPORTS

# 想验证的机制 → 关键词（用于从教学页里挑条目）
TUTORIAL_TOPICS = {
    "技力（ATB）": ["wiki_tut_bat_sp_1", "wiki_tut_bat_sp_1_mb"],
    "重击": ["wiki_tut_bat_heavy_attack_1"],
    "连携技": ["wiki_tut_bat_combo_1", "wiki_tut_bat_combo_2"],
    "终结技": ["wiki_tut_bat_ultimite_skill_1", "wiki_tut_bat_ultimite_skill_2",
            "wiki_tut_bat_ultimite_skill_3"],
    "战技": ["wiki_tut_bat_normal_skill_1"],
    "处决": ["wiki_tut_bat_execute_1"],
    "闪避": ["wiki_tut_bat_dash_1"],
    "失衡": ["wiki_tut_bat_poise_1", "wiki_tut_bat_poise_2"],
    "返还技力": ["wiki_tut_bat_return_1"],
    "连击": ["wiki_tut_bat_combohint_1"],
    "小队作战": ["wiki_tut_bat_team_1"],
}

# 战斗规则 buff：黑板 key 的语义 + 单位换算
RULE_BUFFS = {
    "buff_dung_atb_add_lastnormalattack": ("重击命中回复技力", "atb", 100.0),
    "buff_dung_atb_add_perfectdodge": ("极限闪避回复技力", "atb", 100.0),
    "buff_dung_atb_add_kill": ("击杀回复技力", "atb", 100.0),
    "buff_common_obtain_ultimate_sp": ("施放战技时全队获得终结技能量",
                                       "usp_everyone", 1.0),
    "buff_common_change_atb_max": ("技力上限变化", "value", 1.0),
}

_CLEAN = {
    "<@ba.key>": "", "</>": "", "<@ba.cryst>": "", "<@ba.pd>": "",
    "<#ba.lastcombo>": "", "<#ba.noguard>": "", "<@ba.poise>": "",
    "<@ba.airborne>": "", "<@ba.knockdown>": "", "<@ba.info>": "",
    "<@ba.vup>": "", "<@gd.key>": "", "<@ld.key>": "",
}


def _load(name: str) -> dict | None:
    p = TABLES / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _i18n() -> dict:
    return _load("I18nTextTable_CN.json") or {}


def _text(node, i18n: dict) -> str:
    if not isinstance(node, dict):
        return ""
    t = node.get("text") or ""
    if t:
        return t
    return i18n.get(str(node.get("id"))) or ""


def _clean(s: str) -> str:
    for k, v in _CLEAN.items():
        s = s.replace(k, v)
    return s.strip()


def _walk(node, i18n: dict, out: list[str]) -> None:
    if isinstance(node, dict):
        if "id" in node and "text" in node:
            s = _text(node, i18n)
            if s:
                out.append(s)
            return
        for v in node.values():
            _walk(v, i18n, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, i18n, out)


def collect() -> dict:
    i18n = _i18n()
    tut = _load("WikiTutorialPageTable.json") or {}

    tutorials: dict[str, list[dict]] = {}
    for topic, keys in TUTORIAL_TOPICS.items():
        rows = []
        for k in keys:
            if k not in tut:
                continue
            out: list[str] = []
            _walk(tut[k], i18n, out)
            body = _clean("\n".join(out))
            if body:
                rows.append({"key": k, "text": body})
        if rows:
            tutorials[topic] = rows

    rules = []
    for bid, (label, key, scale) in RULE_BUFFS.items():
        p = BUFFS / f"{bid}.json"
        if not p.exists():
            rules.append({"buff": bid, "label": label, "value": None,
                          "note": "未缓存（先跑 build/sync 或按需下载）"})
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        bb = {x["key"]: x.get("valueDouble") for x in (d.get("blackboard") or [])}
        raw = bb.get(key)
        rules.append({
            "buff": bid,
            "label": label,
            "key": key,
            "raw": raw,
            "value": (raw * scale) if isinstance(raw, (int, float)) else None,
            "blackboard": bb,
        })
    return {"tutorials": tutorials, "rules": rules}


def write_report(data: dict | None = None) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    data = data or collect()
    lines = ["# 游戏内机制原文与数值（数据出处）", "",
             "> 本文件由 `python run.py mechanics` 从客户端表生成，",
             "> 用于核对引擎里的机制假设。文本来自游戏内教学页，数值来自战斗规则 buff。",
             ""]

    lines += ["## 一、规则数值（BuffData 黑板）", "",
              "| 机制 | buff | 黑板值 | 换算后 | 说明 |", "|---|---|---|---|---|"]
    for r in data["rules"]:
        val = r.get("value")
        val_s = f"{val:g}" if isinstance(val, (int, float)) else "—"
        raw = r.get("raw")
        raw_s = f"{raw:g}" if isinstance(raw, (int, float)) else "—"
        note = r.get("note") or f"`{r.get('buff')}`"
        lines.append(f"| {r['label']} | `{r['buff']}` | {raw_s} | {val_s} | {note} |")
    lines += ["", "换算说明：技力（ATB）以 100 为 1 段（`SkillPatchTable` 里战技"
              "`costValue=100`），因此 buff 黑板里的 `atb=1.0` 记为 100 点。", ""]

    lines += ["## 二、机制原文（游戏内教学页）", ""]
    for topic, rows in data["tutorials"].items():
        lines.append(f"### {topic}")
        lines.append("")
        for r in rows:
            lines.append(f"`{r['key']}`")
            lines.append("")
            lines.append("> " + r["text"].replace("\n", "\n> "))
            lines.append("")

    p = REPORTS / "mechanics.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
