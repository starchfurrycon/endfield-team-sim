"""把语义抽取结果写回归一化数据集。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import config
from ..model import Operator
from . import llm, rules

REPORTS = config.REPORTS


def apply_to_operators(operators: dict[str, Operator], *, use_llm: bool = False,
                       verbose: bool = True) -> dict:
    stats = {"skills": 0, "known_trigger": 0, "unknown_trigger": 0,
             "hit_dependent": 0, "llm_used": 0, "llm_rejected": 0,
             "divergences": []}
    llm_on = use_llm and llm.available()

    for cid, op in operators.items():
        hit_dep = False
        combo_trigger = None
        for key, sk in op.skills.items():
            sem = rules.extract_skill(sk.to_dict())
            if llm_on:
                got = llm.annotate_skill(sk.to_dict())
                if got is None:
                    stats["llm_rejected"] += 1
                else:
                    stats["llm_used"] += 1
                    merged = llm.merge(sem, got)
                    if merged.get("notes") and any(
                            str(n).startswith("conflict") for n in merged["notes"]):
                        stats["divergences"].append({
                            "operator": op.name_zh, "skill": key,
                            "notes": [n for n in merged["notes"]
                                      if str(n).startswith("conflict")],
                        })
                    sem = merged
            sk.semantics = sem
            stats["skills"] += 1
            if sem["trigger"] in ("unknown",):
                stats["unknown_trigger"] += 1
            else:
                stats["known_trigger"] += 1
            if sem["needs_enemy_hit"]:
                hit_dep = True
                stats["hit_dependent"] += 1
            if sk.slot == "combo_skill":
                combo_trigger = sem["trigger"]

        op.semantics = {
            "has_hit_dependent_skill": hit_dep,
            "combo_trigger": combo_trigger,
            "skill_count": len(op.skills),
        }

    stats["unknown_rate"] = round(
        stats["unknown_trigger"] / max(1, stats["skills"]), 4)
    if verbose:
        print(f"[semantics] {stats['skills']} 技能，trigger 已知 "
              f"{stats['known_trigger']}，unknownown {stats['unknown_trigger']} "
              f"({stats['unknown_rate'] * 100:.1f}%)，依赖受击 {stats['hit_dependent']}")
    return stats


def write_semantics_report(operators: dict[str, Operator], stats: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# 语义层报告", "",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 技能总数：{stats['skills']}",
             f"- trigger 已识别：{stats['known_trigger']}",
             f"- trigger 未知：{stats['unknown_trigger']} "
             f"（{stats['unknown_rate'] * 100:.1f}%）",
             f"- 依赖受击/格挡：{stats['hit_dependent']} 个技能",
             f"- LLM 调用成功/拒绝：{stats['llm_used']} / {stats['llm_rejected']}",
             ""]

    rows = []
    for cid, op in operators.items():
        for key, sk in op.skills.items():
            s = sk.semantics or {}
            if s.get("trigger") in ("unknown", None):
                rows.append(f"| {op.name_zh} | {key} | {s.get('trigger')} | "
                            f"{s.get('confidence')} |")
    lines += ["## 未识别的触发条件（需人工或 LLM 补）", ""]
    if rows:
        lines += ["| 干员 | 技能 | trigger | 置信度 |", "|---|---|---|---|"]
        lines += rows
    else:
        lines.append("无")
    lines.append("")

    lines += ["## 依赖受击的技能（用户点名的高风险项）", ""]
    hits = []
    for cid, op in operators.items():
        for key, sk in op.skills.items():
            s = sk.semantics or {}
            if s.get("needs_enemy_hit"):
                hits.append(f"| {op.name_zh} | {key} | {s.get('trigger')} | "
                            f"{s.get('duration_s')} |")
    if hits:
        lines += ["| 干员 | 技能 | 触发 | 时长(s) |", "|---|---|---|---|"]
        lines += hits
    else:
        lines.append("无")
    lines.append("")

    if stats.get("divergences"):
        lines += ["## 规则与 LLM 的分歧", ""]
        for d in stats["divergences"]:
            lines.append(f"- {d['operator']} / {d['skill']}: " + "; ".join(d["notes"]))
        lines.append("")

    p = REPORTS / "semantics.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def load_operators() -> dict[str, Operator]:
    out = {}
    d = config.NORM / "operators"
    for p in sorted(d.glob("*.json")):
        op = Operator.load(p)
        out[op.char_id] = op
    return out


def save_operators(operators: dict[str, Operator]) -> None:
    d = config.NORM / "operators"
    d.mkdir(parents=True, exist_ok=True)
    for cid, op in operators.items():
        (d / f"{cid}.json").write_text(
            json.dumps(op.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
