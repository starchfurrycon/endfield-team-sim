"""把语义抽取结果写回归一化数据集。

两条通道：
  * 规则（永远先跑，零成本、可复现）
  * LLM（可选，只补"规则不确定"的技能：未知触发、缺元素、低置信度）

合并策略：规则为主，LLM 只在规则"不知道"时接管；两者冲突时保留规则并记入复核报告。
LLM 的结果必须已经过 schema + 证据校验（见 semantics/llm.py）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import config
from ..model import Operator
from . import llm, llm_config, rules

REPORTS = config.REPORTS

# 只有这些槽位的"缺元素"值得问 LLM（普攻不可能带元素）
_ELEMENT_SLOTS = {"battle_skill", "combo_skill", "ultimate"}


def _needs_llm(sem: dict, sk) -> bool:
    if sem.get("trigger") in (None, "unknown"):
        return True
    if sem.get("element") is None and sk.slot in _ELEMENT_SLOTS:
        return True
    if float(sem.get("confidence") or 0) < 0.6 and (sem.get("text_zh") or ""):
        return True
    return False


def merge(rule: dict, got: dict) -> tuple[dict, list[dict], list[str]]:
    """规则为主、LLM 补空。返回 (合并结果, 变更列表, 冲突列表)。"""
    out = dict(rule)
    changes: list[dict] = []
    conflicts: list[str] = []

    def take(field: str, reason_empty=True):
        rv, lv = rule.get(field), got.get(field)
        if lv in (None, "unknown", ""):
            return
        if reason_empty and rv in (None, "unknown", ""):
            out[field] = lv
            changes.append({"field": field, "before": rv, "after": lv})
        elif rv != lv:
            conflicts.append(f"{field}: rule={rv} llm={lv}")

    take("trigger")
    take("element")
    take("abnormal")
    take("target")
    if got.get("role") and out.get("role") in (None, "unknown"):
        out["role"] = got["role"]
        changes.append({"field": "role", "before": out.get("role"), "after": got["role"]})

    for field in ("needs_enemy_hit", "heals", "shields", "is_dot"):
        if got.get(field) and not out.get(field):
            out[field] = True
            changes.append({"field": field, "before": False, "after": True})

    if out.get("duration_s") is None and got.get("duration_s") is not None:
        out["duration_s"] = got["duration_s"]
        changes.append({"field": "duration_s", "before": None,
                        "after": got["duration_s"]})

    tags = list(dict.fromkeys((out.get("tags") or []) + (got.get("tags") or [])))[:10]
    if tags != (out.get("tags") or []):
        out["tags"] = tags
    if got.get("evidence"):
        out["evidence"] = got["evidence"]
    if got.get("trigger_params") and out.get("trigger_params") != got["trigger_params"]:
        merged = dict(out.get("trigger_params") or {})
        for k, v in got["trigger_params"].items():
            if v not in (None, "") and merged.get(k) in (None, ""):
                merged[k] = v
        out["trigger_params"] = merged

    out["method"] = "merged" if changes else "rule"
    if changes:
        out["confidence"] = round(max(float(rule.get("confidence") or 0),
                                      float(got.get("confidence") or 0)), 2)
    if conflicts:
        out["notes"] = list(out.get("notes") or []) + ["conflict " + c for c in conflicts]
        out["confidence"] = round(min(float(out.get("confidence") or 0.5), 0.5), 2)
    return out, changes, conflicts


def _cost(stats: dict, cfg: dict) -> float:
    pin = float(cfg.get("price_in") or 0)
    pout = float(cfg.get("price_out") or 0)
    return round(stats.get("prompt_tokens", 0) / 1e6 * pin
                 + stats.get("completion_tokens", 0) / 1e6 * pout, 4)


def apply_to_operators(operators: dict[str, Operator], *, use_llm: bool = False,
                       mode: str = "gaps", limit: int | None = None,
                       verbose: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or llm_config.load()
    stats: dict = {"skills": 0, "known_trigger": 0, "unknown_trigger": 0,
                   "hit_dependent": 0, "llm_used": 0, "llm_rejected": 0,
                   "llm_cache": 0, "llm_calls": 0, "repaired": 0,
                   "prompt_tokens": 0, "completion_tokens": 0,
                   "llm_enabled": bool(use_llm and llm.available(cfg)),
                   "mode": mode, "prompt_version": llm.PROMPT_VERSION,
                   "changes": [], "conflicts": [], "divergences": []}
    usage: dict = {}
    asked = 0

    for cid, op in operators.items():
        hit_dep = False
        combo_trigger = None
        for key, sk in op.skills.items():
            sem = rules.extract_skill(sk.to_dict())
            if stats["llm_enabled"]:
                want = mode == "all" or _needs_llm(sem, sk)
                if want and (limit is None or asked < limit):
                    asked += 1
                    got = llm.annotate_skill(sk.to_dict(), cfg=cfg, usage_out=usage)
                    if got is None:
                        stats["llm_rejected"] += 1
                    else:
                        stats["llm_used"] += 1
                        merged, changes, conflicts = merge(sem, got)
                        if changes:
                            stats["changes"].append({
                                "operator": op.name_zh, "skill": key,
                                "slot": sk.slot, "confidence": got.get("confidence"),
                                "evidence": got.get("evidence") or [],
                                "changes": changes,
                            })
                        if conflicts:
                            stats["conflicts"].append({
                                "operator": op.name_zh, "skill": key,
                                "notes": conflicts,
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

    stats["llm_cache"] = int(usage.get("cache") or 0)
    stats["llm_calls"] = int(usage.get("calls") or 0)
    stats["repaired"] = int(usage.get("repaired") or 0)
    stats["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
    stats["completion_tokens"] = int(usage.get("completion_tokens") or 0)
    stats["cost"] = _cost(stats, cfg)
    stats["unknown_rate"] = round(
        stats["unknown_trigger"] / max(1, stats["skills"]), 4)
    if verbose:
        llm_line = ""
        if stats["llm_enabled"]:
            llm_line = (f"，LLM 采纳 {stats['llm_used']} / 拒绝 {stats['llm_rejected']}，"
                        f"缓存命中 {stats['llm_cache']}，tokens "
                        f"{stats['prompt_tokens']}+{stats['completion_tokens']}")
        print(f"[semantics] {stats['skills']} 技能，trigger 已知 "
              f"{stats['known_trigger']}，unknown {stats['unknown_trigger']} "
              f"({stats['unknown_rate'] * 100:.1f}%)，依赖受击 {stats['hit_dependent']}"
              f"{llm_line}")
    return stats


def write_llm_review(operators: dict[str, Operator], stats: dict,
                     cfg: dict | None = None) -> Path:
    """LLM 复核报告：改了什么、依据是什么、哪些还需要人看。"""
    cfg = cfg or llm_config.load()
    pub = llm_config.public(cfg)
    REPORTS.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    add = L.append
    add("# LLM 语义复核报告")
    add("")
    add(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"- 模式：`{stats.get('mode')}` · prompt `{stats.get('prompt_version')}`")
    add(f"- 模型：`{pub['model']}` · 接口：`{pub['base']}` · 密钥：`{pub['key_masked'] or '未配置'}`")
    add(f"- 采纳 / 拒绝：{stats.get('llm_used', 0)} / {stats.get('llm_rejected', 0)}"
        f"（缓存命中 {stats.get('llm_cache', 0)}，修复重试 {stats.get('repaired', 0)}）")
    add(f"- tokens：输入 {stats.get('prompt_tokens', 0)} / 输出 {stats.get('completion_tokens', 0)}"
        + (f" · 估算成本 {stats.get('cost')}" if stats.get("cost") else ""))
    add("")
    add("> LLM **不产出任何数值**：只填 trigger / element / abnormal / role 这类类别。"
        "所有倍率与消耗仍来自客户端表。")
    add("")

    add("## 1. 被 LLM 改写的技能")
    add("")
    changes = stats.get("changes") or []
    if not changes:
        add("无（规则层已足够，或未启用 LLM）")
    else:
        add("| 干员 | 技能 | 槽位 | 字段 | 原值 → 新值 | 置信度 | 证据 |")
        add("|---|---|---|---|---|---|---|")
        for row in changes:
            for ch in row["changes"]:
                ev = " / ".join(str(x) for x in (row.get("evidence") or [])[:2])
                add(f"| {row['operator']} | {row['skill']} | {row['slot']} | "
                    f"{ch['field']} | `{ch['before']}` → `{ch['after']}` | "
                    f"{row.get('confidence')} | {ev[:60]} |")
    add("")

    add("## 2. 规则与 LLM 冲突（保留规则值，需人工确认）")
    add("")
    conflicts = stats.get("conflicts") or []
    if not conflicts:
        add("无")
    else:
        for c in conflicts:
            add(f"- {c['operator']} / {c['skill']}：" + "；".join(c["notes"]))
    add("")

    add("## 3. 仍为 unknown / 低置信度的技能（LLM 也没把握）")
    add("")
    rows = []
    for cid, op in operators.items():
        for key, sk in op.skills.items():
            s = sk.semantics or {}
            if s.get("trigger") in ("unknown", None) or float(s.get("confidence") or 0) < 0.5:
                rows.append(f"| {op.name_zh} | {key} | {s.get('trigger')} | "
                            f"{s.get('confidence')} | {s.get('method')} |")
    if rows:
        add("| 干员 | 技能 | trigger | 置信度 | 来源 |")
        add("|---|---|---|---|---|")
        L.extend(rows)
    else:
        add("无")
    add("")

    add("## 4. 怎么复核")
    add("")
    add("1. 看上面的「被改写」表，逐条核对证据（evidence 必须是原文子串）；")
    add("2. 证据明确但结果不对 → 改 `src/endfield/data/curated.json`（优先级最高）；")
    add("3. 提示词本身有问题 → 改 `src/endfield/semantics/prompts/semantics_system.md`，")
    add("   并把 `llm.py:PROMPT_VERSION` 加 1（否则旧缓存会被复用）。")
    add("")

    p = REPORTS / "llm-review.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p


def write_semantics_report(operators: dict[str, Operator], stats: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# 语义层报告", "",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 技能总数：{stats['skills']}",
             f"- trigger 已识别：{stats['known_trigger']}",
             f"- trigger 未知：{stats['unknown_trigger']} "
             f"（{stats['unknown_rate'] * 100:.1f}%）",
             f"- 依赖受击/格挡：{stats['hit_dependent']} 个技能",
             f"- LLM：{'启用' if stats.get('llm_enabled') else '未启用'}"
             f"（采纳 {stats.get('llm_used', 0)} / 拒绝 {stats.get('llm_rejected', 0)}）",
             "- 复核明细见 `llm-review.md`",
             ""]

    rows = []
    for cid, op in operators.items():
        for key, sk in op.skills.items():
            s = sk.semantics or {}
            if s.get("trigger") in ("unknown", None):
                rows.append(f"| {op.name_zh} | {key} | {s.get('trigger')} | "
                            f"{s.get('confidence')} | {s.get('method')} |")
    lines += ["## 未识别的触发条件（需人工或 LLM 补）", ""]
    if rows:
        lines += ["| 干员 | 技能 | trigger | 置信度 | 来源 |", "|---|---|---|---|---|"]
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
