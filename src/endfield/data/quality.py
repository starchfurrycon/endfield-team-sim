"""数据自检报告。

回答一个具体问题：**数据更新后，这套无 LLM 的流水线还知道自己在算什么吗？**
把"哪些是权威来源、哪些是兜底、哪些根本没建模"逐条列出来，而不是藏在代码里。

输出：data/reports/data-quality.md（`build` 与 `check` 都会写）。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .. import config
from ..model import Dataset

# 引擎认识的套装黑板 key（与 Battle._equip_acc 保持一致）
_SUIT_KEYS = {"str", "agi", "wisd", "will", "atk", "atk_up", "dmg_up", "dmg"}

# 已知但故意不建模的属性（比"未知"更准确的说法）
_KNOWN_UNMODELED = {
    0: "等级（占位字段，不参与公式）",
    9: "暴击率（用 CRIT_RATE_BASE 期望值，不逐条叠加）",
}


def _equip() -> dict:
    try:
        return json.loads((config.NORM / "equip.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"items": {}, "suits": {}}


def _categorizer(ds: Dataset):
    """用引擎自己的归类逻辑判断属性 id 有没有被建模。"""
    from ..engine.battle import Battle, Scenario

    cid = next(iter(ds.operators), None)
    if not cid:
        return lambda _a: None
    b = Battle(ds, Scenario(team=[cid], duration_s=1))
    return b._attr_category


def _known_suit_key(key: str) -> bool:
    return key in _SUIT_KEYS or key.endswith("_dmg_up") or key.endswith("_up")


def llm_items(ds: Dataset, limit: int = 40) -> list[dict]:
    """收集"引擎不认识的东西"，供 LLM 归类（只解释，不改引擎）。"""
    equip = _equip()
    categorize = _categorizer(ds)
    meta = (ds.constants or {}).get("attr_meta") or {}
    items: list[dict] = []

    # 套装被动里不认识的 key
    seen: dict[str, dict] = {}
    for sid, srow in (equip.get("suits") or {}).items():
        for k, v in (srow.get("passive_bb") or {}).items():
            if _known_suit_key(k):
                continue
            row = seen.setdefault(k, {"key": k, "samples": [], "context": "套装被动"})
            if len(row["samples"]) < 3:
                row["samples"].append(v)
            if sid not in row["context"]:
                row["context"] += f" / {sid}"
    items.extend(seen.values())

    # 武器条件型技能
    wseen = {}
    for op in ds.operators.values():
        for sid in ((op.weapon or {}).get("skills") or []):
            if str(sid).startswith("sk_wpn_") and sid not in wseen:
                wseen[sid] = {"key": str(sid), "samples": [],
                              "context": f"武器条件型技能（{op.name_zh} 默认武器）"}
    items.extend(list(wseen.values())[:10])

    # 未定性属性 id
    used: Counter = Counter()
    for row in (equip.get("items") or {}).values():
        for k in (row.get("attrs") or {}):
            try:
                used[int(k)] += 1
            except (TypeError, ValueError):
                continue
    for a, n in used.items():
        if categorize(a) is None:
            icon = (meta.get(str(a)) or {}).get("icon", "")
            items.append({"key": f"attrType {a}", "samples": [], "context": f"装备属性（出现 {n} 次，icon={icon}）"})
    return items[:limit]


def write_llm_mechanics(rows: list[dict], model: str = "") -> Path:
    config.ensure_dirs()
    lines = ["# LLM 机制归类（未建模项）", "",
             "> LLM 只给**解释与分类**，不会修改引擎；这份报告用来判断"
             "下一版该补哪些机制。", ""]
    if model:
        lines += [f"- 模型：`{model}`", ""]
    lines += ["| key | 归类 | 引擎是否建模 | 含义 | 置信度 |",
              "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| `{r['key']}` | {r['category']} | "
                     f"{'是' if r['modeled_by_engine'] else '否'} | "
                     f"{r['meaning']} | {r['confidence']} |")
    lines.append("")
    unmodeled = [r for r in rows if not r["modeled_by_engine"]]
    if unmodeled:
        lines += ["## 下一版可以考虑补的", ""]
        for r in unmodeled:
            lines.append(f"- `{r['key']}`（{r['category']}）：{r['meaning']}")
        lines.append("")
    p = config.REPORTS / "mechanics-review.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_report(ds: Dataset, manifest: dict | None = None) -> Path:
    config.ensure_dirs()
    manifest = manifest or {}
    cov = ds.coverage or {}
    categorize = _categorizer(ds)

    # ---- 属性 id 使用情况
    equip = _equip()
    used_attrs: Counter = Counter()
    for row in (equip.get("items") or {}).values():
        for k in (row.get("attrs") or {}):
            try:
                used_attrs[int(k)] += 1
            except (TypeError, ValueError):
                continue
    for op in ds.operators.values():
        for entries in ((op.mods or {}).get("talents") or {}).values():
            for lv in entries.values():
                for e in lv or []:
                    if e.get("kind") == "attr" and e.get("attr") is not None:
                        used_attrs[int(e["attr"])] += 1
        for entries in ((op.mods or {}).get("potentials") or {}).values():
            for e in entries or []:
                if e.get("kind") == "attr" and e.get("attr") is not None:
                    used_attrs[int(e["attr"])] += 1
    meta = (ds.constants or {}).get("attr_meta") or {}
    unmodeled_attrs = []
    for a, n in sorted(used_attrs.items()):
        if categorize(a) is None:
            unmodeled_attrs.append((a, n, (meta.get(str(a)) or {}).get("icon", "")))

    # ---- 套装被动 key
    bad_suit_keys: Counter = Counter()
    for srow in (equip.get("suits") or {}).values():
        for k in (srow.get("passive_bb") or {}):
            if not _known_suit_key(k):
                bad_suit_keys[k] += 1

    # ---- 武器条件型技能
    cond_weapons = []
    for op in ds.operators.values():
        for sid in ((op.weapon or {}).get("skills") or []):
            if str(sid).startswith("sk_wpn_"):
                cond_weapons.append((op.name_zh or op.char_id, sid))

    # ---- 同名分版
    by_name: dict[str, list] = {}
    for op in ds.operators.values():
        if op.name_zh:
            by_name.setdefault(op.name_zh, []).append(op.char_id)
    dupes = {k: v for k, v in by_name.items() if len(v) > 1}

    curated = manifest.get("curated") or {}
    avatars = manifest.get("avatars") or {}

    lines: list[str] = []
    add = lines.append
    add("# 数据自检（data-quality）")
    add("")
    add(f"> 生成：{time.strftime('%Y-%m-%d %H:%M:%S')} · 数据集 `{ds.version}` · "
        f"schema {config.SCHEMA_VERSION}")
    add(">")
    add("> 这份报告的目的：数据更新（新干员/新装备/新机制）之后，"
        "一眼看出**哪些还能自动算、哪些已经降级成兜底、哪些根本没建模**。")
    add("")

    add("## 1. 覆盖概况")
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    add(f"| 干员 | {cov.get('operators')} |")
    add(f"| 有 Wiki 中文文本 | {cov.get('with_wiki_text')} |")
    add(f"| 技能总数 | {cov.get('skills_total')} |")
    add(f"| 技能自带元素 | {cov.get('skills_with_explicit_element')} |")
    add(f"| 元素来自 Wiki / 客户端 / 人工修正 | "
        f"{cov.get('operators', 0) - len(cov.get('element_from_client') or []) - len(cov.get('element_from_curated') or [])}"
        f" / {len(cov.get('element_from_client') or [])} / {len(cov.get('element_from_curated') or [])} |")
    if avatars:
        add(f"| 头像 | {avatars.get('ok', 0)} 新下载 / {avatars.get('cached', 0)} 缓存 / "
            f"{avatars.get('missing', 0)} 缺失 |")
    add("")

    add("## 2. 需要人看一眼的地方")
    add("")
    client_only = cov.get("element_from_client") or []
    if client_only:
        names = [ds.operators[c].name_zh or c for c in client_only if c in ds.operators]
        add(f"- **元素只有客户端来源（{len(client_only)}）**："
            f"{'、'.join(names)} —— 这些干员的 wiki 页面缺失，属性只能信客户端表；"
            "新干员如果连表里都是占位，就会在这里暴露。")
    else:
        add("- 元素全部有 Wiki 或人工核对来源。")

    fallback = cov.get("skills_element_fallback") or []
    if fallback:
        add(f"- **技能自身没写元素、按干员元素兜底（{len(fallback)} 个）**：")
        add("")
        add(f"  `{'`、`'.join(fallback[:12])}`" + (" …" if len(fallback) > 12 else ""))
        add("")
        add("  这类最容易算错：如果技能实际是异元素（例如噗切娜的战技是自然伤害），"
            "伤害与附着都会被记成干员元素。证据明确时写进 "
            "`src/endfield/data/curated.json`。")

    if curated:
        add("")
        add(f"- **人工修正生效（{len(curated)}）**：")
        for cid, notes in sorted(curated.items()):
            name = ds.operators[cid].name_zh if cid in ds.operators else cid
            add(f"  - {name}（`{cid}`）：{'；'.join(notes)}")

    if unmodeled_attrs:
        known = [(a, n, i) for a, n, i in unmodeled_attrs if a in _KNOWN_UNMODELED]
        unknown = [(a, n, i) for a, n, i in unmodeled_attrs if a not in _KNOWN_UNMODELED]
        if known:
            add("")
            add(f"- **已知但不建模的属性（{len(known)}）**：")
            for a, n, _ in known:
                add(f"  - `{a}`：{_KNOWN_UNMODELED[a]}（出现 {n} 次）")
        add("")
        if unknown:
            add(f"- **未定性的属性 id（{len(unknown)}）**：不会参与伤害计算，也不会脏进通用增伤。")
            add("")
            add("| id | 出现次数 | iconName |")
            add("|---|---|---|")
            for a, n, icon in unknown:
                add(f"| {a} | {n} | `{icon or '（无）'}` |")
        else:
            add("- **没有未定性的属性 id**：出现的属性都能归到确定的乘区。")
    else:
        add("")
        add("- 所有出现的属性 id 都能归类。")

    if bad_suit_keys:
        add("")
        add(f"- **套装被动里引擎不认识的 key（{len(bad_suit_keys)}）**："
            f"`{'`、`'.join(sorted(bad_suit_keys)[:10])}`")

    if cond_weapons:
        add("")
        add(f"- **武器条件型技能（{len(cond_weapons)} 条，未建模）**："
            f"`{'`、`'.join(sid for _, sid in cond_weapons[:6])}` …"
            "——它们是“攻击力+X% 持续若干秒”这类触发型被动，目前不施加（宁可低估）。")

    if dupes:
        add("")
        add(f"- **同名多分版（{len(dupes)}）**："
            + "；".join(f"{k}：`{'`/`'.join(v)}`" for k, v in dupes.items())
            + " —— UI 只显示技能最全的一份，其余记在 variants。")

    add("")
    add("## 3. 判定：数据更新后还能不能算")
    add("")
    add("| 更新类型 | 自动程度 | 说明 |")
    add("|---|---|---|")
    add("| 新干员（表 + SkillData 齐全） | 全自动 | 帧、倍率、消耗、属性都来自表；"
        "缺 wiki 文本时元素退回客户端 charTypeId（会在上面列出） |")
    add("| 新武器 / 新装备 / 新套装 | 全自动 | 属性向量与套装黑板由 build 解析；"
        "新属性 id 先按 iconName 归类，归不了就进“未定性”表 |")
    add("| 新技能元素（同干员异元素技能） | 半自动 | wiki 有「战技属性」等字段就自动；"
        "没有就需要 curated 补一条 |")
    add("| 新连携触发条件 | 半自动 | 规则层有常见触发词；未知条件会落 `unknown`，"
        "结果里显示、不会假装认识 |")
    add("| 新伤害机制（新 DoT / 新异常 / 新乘区） | 不自动 | 需要改引擎；"
        "未建模项会出现在每次输出的 `unmodeled` 里 |")
    add("| buff/debuff 体系 | 不自动 | 同上（BuffData 黑板叠加未建模） |")
    add("")

    p = config.REPORTS / "data-quality.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
