"""结果表达层：Markdown 报告 + 人话解释。

无 LLM 时用模板生成（保证离线可用）；有 LLM 时把结构化结果交给它润色，
但**数值一律由本模块注入**，LLM 不允许改写数字。
"""

from __future__ import annotations

import json
import os

from .. import config
from ..model import Dataset
from ..engine.battle import Result


def _fmt(x: float) -> str:
    return f"{x:,.0f}"


def axis_markdown(res: Result, ds: Dataset | None, title: str) -> str:
    lines = [f"## {title}", ""]
    lines.append(f"- 总伤害：**{_fmt(res.total_damage)}**")
    lines.append(f"- 窗口：{res.duration_frames / config.FPS:.1f}s，"
                 f"DPS：**{_fmt(res.dps)}**")
    lines.append(f"- 合法性：{'通过' if res.legality_ok else '有问题'}")
    if res.legality_issues:
        for iss in res.legality_issues[:5]:
            lines.append(f"  - {iss}")
    lines += ["", "### 输出轴", "",
              "| 时间(s) | 结束(s) | 动作 | 命中帧(s) |", "|---|---|---|---|"]
    for row in _axis_rows(res):
        hits = ", ".join(f"{h:.2f}" for h in row.get("hits") or [])
        lines.append(f"| {row['t']:.2f} | {row['t_end']:.2f} | {row['label']} | {hits} |")
    lines += ["", "### 角色贡献", "", "| 干员 | 伤害 | 占比 |", "|---|---|---|"]
    total = max(1.0, res.total_damage)
    for cid, d in sorted(res.per_operator.items(), key=lambda x: -x[1]):
        name = ds.operators[cid].name_zh if ds and cid in ds.operators else cid
        lines.append(f"| {name} | {_fmt(d)} | {d / total * 100:.1f}% |")
    if res.warnings:
        lines += ["", "### 注意", ""]
        lines += [f"- {w}" for w in res.warnings]
    if res.unmodeled:
        lines += ["", "### 未建模项（数值会偏低，仅可用于相对比较）", ""]
        lines += [f"- {u}" for u in res.unmodeled]
    return "\n".join(lines)


def _axis_rows(res: Result) -> list[dict]:
    rows = []
    for ev in res.timeline:
        if ev.get("kind") not in ("cast", "enemy_attack", "stagger"):
            continue
        rows.append({
            "t": ev["frame"] / config.FPS,
            "t_end": ev.get("commit_end", ev["frame"]) / config.FPS,
            "label": ev.get("label") or ev["kind"],
            "hits": [h / config.FPS for h in (ev.get("hits") or [])],
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def explain_rotation(res: Result, ds: Dataset | None) -> str:
    """不依赖 LLM 的"为什么这样打"。"""
    counts = (res.stats or {}).get("cast_counts", {})
    parts = []
    if counts.get("ultimate"):
        parts.append(f"整场共放 {counts['ultimate']} 次终结技")
    if counts.get("combo_skill"):
        parts.append(f"{counts['combo_skill']} 次连携技")
    if counts.get("battle_skill"):
        parts.append(f"{counts['battle_skill']} 次战技")
    casts = counts.get("normal_attack", 0) + counts.get("power_attack", 0)
    if casts:
        parts.append(f"{casts} 段普攻/重击用于填充与回技力")
    head = "、".join(parts) if parts else "没有产生有效动作"

    # 主输出角色
    total = max(1.0, res.total_damage)
    top = sorted(res.per_operator.items(), key=lambda x: -x[1])[:2]
    names = []
    for cid, d in top:
        nm = ds.operators[cid].name_zh if ds and cid in ds.operators else cid
        names.append(f"{nm}（{d / total * 100:.0f}%）")
    main_line = f"伤害主要来自 {'、'.join(names)}。" if names else ""
    return f"{head}。{main_line}"


def llm_polish(text: str, payload: dict) -> str:
    """可选：让 LLM 润色，但数字以注入的结构化结果为准。"""
    key = os.environ.get("ENDFIELD_LLM_API_KEY")
    if not key:
        return text
    try:
        import urllib.request
        base = (os.environ.get("ENDFIELD_LLM_BASE")
                or "https://api.deepseek.com/v1").rstrip("/")
        model = os.environ.get("ENDFIELD_LLM_MODEL") or "deepseek-chat"
        body = {
            "model": model, "temperature": 0.2,
            "messages": [
                {"role": "system",
                 "content": "你是终末地配队顾问。把给定的战斗结果改写成简洁中文建议。"
                            "禁止改动任何数字，禁止编造数据中没有的数值。"},
                {"role": "user", "content": json.dumps(
                    {"facts": payload, "draft": text}, ensure_ascii=False)},
            ],
        }
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode())
        return resp["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return text
