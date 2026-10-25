"""覆盖率与漂移报告。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import config

REPORTS = config.REPORTS


def write_coverage(dataset: dict, manifest: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    cov = dataset.get("coverage") or {}
    lines = [
        "# 数据覆盖率报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据集版本：`{dataset.get('version')}`",
        f"- 数据源：`{(manifest.get('github') or {}).get('repo')}` @ "
        f"`{(manifest.get('github') or {}).get('short_sha')}` "
        f"({(manifest.get('github') or {}).get('date')})",
        f"- Wiki：{((manifest.get('wiki') or {}).get('generator') or 'n/a')}，"
        f"articles={((manifest.get('wiki') or {}).get('articles') or 'n/a')}",
        "",
        "## 总览",
        "",
        "| 指标 | 数量 |",
        "|---|---|",
        f"| 干员总数 | {cov.get('operators', 0)} |",
        f"| 有完整帧数据 | {cov.get('with_full_timing', 0)} |",
        f"| 有技能倍率 | {cov.get('with_multipliers', 0)} |",
        f"| 有 wiki 文本 | {cov.get('with_wiki_text', 0)} |",
        "",
    ]
    for key, label in (("missing_normal_chain", "普攻段数 < 3"),
                       ("missing_power_attack", "缺重击"),
                       ("missing_battle_skill", "缺战技")):
        vals = cov.get(key) or []
        lines.append(f"## {label}（{len(vals)}）")
        lines.append("")
        if vals:
            lines.append("、".join(f"`{v}`" for v in vals))
        else:
            lines.append("无")
        lines.append("")

    only = cov.get("wiki_only") or []
    lines.append(f"## 仅 wiki 有、客户端表里没有（{len(only)}）")
    lines.append("")
    lines.append("、".join(f"`{v}`" for v in only) if only else "无")
    lines.append("")
    lines.append("> 说明：出现在这里说明该干员可能尚未进入当前版本的客户端表，")
    lines.append("> 或 wiki 使用了别名。二者都需人工核对。")
    lines.append("")

    p = REPORTS / "coverage.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def diff_manifest(prev: dict | None, cur: dict) -> dict:
    out: dict = {"changed": False, "keys": {}}
    if not prev:
        out["changed"] = True
        out["reason"] = "首次构建"
        return out
    for k in ("version", "operators", "operators_with_wiki"):
        if prev.get(k) != cur.get(k):
            out["changed"] = True
            out["keys"][k] = [prev.get(k), cur.get(k)]
    pg = (prev.get("github") or {}).get("sha")
    cg = (cur.get("github") or {}).get("sha")
    if pg != cg:
        out["changed"] = True
        out["keys"]["github_sha"] = [pg, cg]
    return out


def write_drift(prev: dict | None, cur: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    d = diff_manifest(prev, cur)
    lines = ["# 数据漂移", "", f"- 检测时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 是否有变化：**{'是' if d['changed'] else '否'}**", ""]
    if d.get("reason"):
        lines.append(f"- 原因：{d['reason']}")
    if d.get("keys"):
        lines += ["", "| 字段 | 旧 | 新 |", "|---|---|---|"]
        for k, (a, b) in d["keys"].items():
            lines.append(f"| {k} | `{a}` | `{b}` |")
    lines.append("")
    p = REPORTS / "drift.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def load_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 交叉校验

def _pct(x) -> float | None:
    if x is None:
        return None
    s = str(x).strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def crosscheck_skilldata(operators: dict, wiki_skilldata: dict) -> dict:
    """把本地倍率与 wiki MediaWiki:Skilldata.json 抽样比对。

    比对项（语义一致、可无条件对齐）：
      普攻第一段倍率 / 战技效果1(atk_scale) / 所需终结技能量 / 连携技冷却时间
    """
    checks: list[dict] = []
    matched = mismatched = skipped = 0

    for cid, op in operators.items():
        name = op.name_zh
        w = wiki_skilldata.get(name) if name else None
        if not w:
            skipped += 1
            continue
        pairs: list[tuple[str, object, object]] = []

        a1 = op.skills.get(f"{cid}_attack1")
        if a1 and a1.levels:
            pairs.append(("普攻第一段", _pct((w.get("普攻第一段倍率") or [None])[0]),
                          round(a1.total_multiplier(1, prefer_wiki=False) * 100, 4)))
        bs = next((s for s in op.skills.values() if s.slot == "battle_skill"), None)
        if bs and bs.levels:
            pairs.append(("战技效果1", _pct((w.get("战技效果1") or [None])[0]),
                          round(bs.total_multiplier(1, prefer_wiki=False) * 100, 4)))
        ult = next((s for s in op.skills.values() if s.slot == "ultimate"), None)
        if ult and ult.levels:
            pairs.append(("所需终结技能量", _pct((w.get("所需终结技能量") or [None])[0]),
                          ult.levels[0].ult_cost))
        combo = next((s for s in op.skills.values() if s.slot == "combo_skill"), None)
        if combo and combo.levels:
            pairs.append(("连携技冷却", _pct((w.get("连携技冷却时间") or [None])[0]),
                          combo.levels[0].cooldown_s))

        for field, wv, lv in pairs:
            if wv is None or lv is None:
                continue
            ok = abs(float(wv) - float(lv)) < 1e-6
            checks.append({"operator": name, "field": field,
                           "wiki": wv, "local": lv, "ok": ok})
            if ok:
                matched += 1
            else:
                mismatched += 1

    return {
        "matched": matched,
        "mismatched": mismatched,
        "operators_skipped": skipped,
        "details": [c for c in checks if not c["ok"]][:50],
        "rate": round(matched / max(1, matched + mismatched), 4),
    }


def write_crosscheck(result: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# 数据交叉校验（本地表 vs Wiki Skilldata）", "",
             f"- 一致：{result['matched']} 项",
             f"- 不一致：{result['mismatched']} 项",
             f"- 一致率：**{result['rate'] * 100:.2f}%**",
             f"- 缺 wiki 数据而跳过：{result['operators_skipped']} 人", ""]
    if result["details"]:
        lines += ["| 干员 | 字段 | wiki | 本地 |", "|---|---|---|---|"]
        for d in result["details"]:
            lines.append(f"| {d['operator']} | {d['field']} | {d['wiki']} | {d['local']} |")
    else:
        lines.append("无不一致项。")
    lines.append("")
    p = REPORTS / "crosscheck.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p

def write_constants() -> Path:
    """导出所有可调常量的来源，区分「数据口径」与「工程估计」。"""
    from .. import config as cfg
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# 常量来源表", "",
             "引擎用到的所有外部常量。`source` 以 `BufferData:` / `BattleConst` /",
             "`wiki` 开头的属于**有据可依**；`estimate` / `assumption` 的是工程猜测，",
             "需要实测校准。", "",
             "| 常量 | 值 | 来源 | 说明 |", "|---|---|---|---|"]
    for name, meta in cfg.GUESSED.items():
        src = str(meta.get("source", ""))
        mark = "估计" if src in ("estimate", "assumption") else "数据"
        val = meta.get("value")
        note = str(meta.get("note", ""))[:60]
        lines.append(f"| `{name}` | {val} | {mark} / {src} | {note} |")
    est = sum(1 for m in cfg.GUESSED.values()
              if str(m.get("source")) in ("estimate", "assumption"))
    lines += ["", f"合计 {len(cfg.GUESSED)} 个常量，其中 **{est} 个仍是估计值**。", ""]
    lines += ["## 已知仍在数据里但尚未接入的", "",
              "- `buff_common_obtain_ultimate_sp.usp_self`（战技给自身的额外能量）",
              "- 各干员 `usp_1..usp_4`（连携技按段给能量，如莱万汀 25/5/5/0）",
              "- `BattleConst.maxUltimateSp`（默认能量上限 100）",
              "- BuffData 里的 ATB（技力）恢复速率修正，需要先找到基础速率", ""]
    p = REPORTS / "constants.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
