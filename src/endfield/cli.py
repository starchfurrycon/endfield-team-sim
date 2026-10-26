"""命令行入口。

  python run.py sync      # 拉取/刷新数据源
  python run.py build     # raw -> normalized
  python run.py check     # 覆盖率/漂移/完整性
  python run.py axis      # 单队输出轴（P3）
  python run.py team      # 配队推荐（P3）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .data import build as build_mod
from .data import coverage as cov_mod
from .data import github, wiki


def cmd_sync(args) -> int:
    config.ensure_dirs()
    src = github.GithubSource()
    head = src.head(refresh=True)
    print(f"[sync] github {head['repo']}@{head['short_sha']} ({head.get('date')})")
    for name in config.TABLES:
        try:
            t = src.table(name, force=args.force)
            n = len(t) if hasattr(t, "__len__") else 0
            print(f"  ok  {name:32s} entries={n}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {name:32s} {type(e).__name__}: {e}")
    try:
        idx = wiki.operator_index(force=args.force)
        meta = wiki.site_meta(force=args.force)
        print(f"[sync] wiki {meta.get('generator')} operators={len(idx)}")
    except Exception as e:  # noqa: BLE001
        print(f"[sync] wiki 不可用：{type(e).__name__}: {e}")
        if args.strict:
            return 2
    return 0


def cmd_semantics(args) -> int:
    from .semantics import apply as sem_apply
    ops = sem_apply.load_operators()
    if not ops:
        print("[semantics] 没有数据集，先运行 build")
        return 1
    stats = sem_apply.apply_to_operators(ops, use_llm=args.llm)
    sem_apply.save_operators(ops)
    p = sem_apply.write_semantics_report(ops, stats)
    print(f"[semantics] 报告：{p}")
    return 0


def cmd_build(args) -> int:
    prev_path = config.NORM / "manifest.json"
    prev = cov_mod.load_manifest(prev_path)
    manifest = build_mod.build(force=args.force, verbose=True)
    dataset = json.loads((config.NORM / "dataset.json").read_text(encoding="utf-8"))
    p1 = cov_mod.write_coverage(dataset, manifest)
    p2 = cov_mod.write_drift(prev, manifest)
    print(f"[build] 报告：{p1.name}, {p2.name}, data-quality.md")

    if not args.no_semantics:
        from .semantics import apply as sem_apply
        ops = sem_apply.load_operators()
        stats = sem_apply.apply_to_operators(ops, use_llm=args.llm)
        sem_apply.save_operators(ops)
        sem_apply.write_semantics_report(ops, stats)
    try:
        from .data import mechanics
        mechanics.write_report()
    except Exception as e:  # noqa: BLE001
        print(f"[build] mechanics 报告跳过：{type(e).__name__}")
    return 0


def _load_dataset():
    from .model import Dataset
    if not (config.NORM / "dataset.json").exists():
        print("[error] 没有数据集，先运行：python run.py build")
        return None
    return Dataset.load(config.NORM)


def cmd_axis(args) -> int:
    from .engine.battle import Scenario
    from .report import explain
    from .search import loadout as lo
    from .search.rotations import search_rotation

    ds = _load_dataset()
    if ds is None:
        return 1
    ids = []
    for n in args.team.split(","):
        op = ds.resolve(n.strip())
        if op is None:
            print(f"[error] 未知干员：{n}")
            return 2
        ids.append(op.char_id)
    if len(ids) > 4:
        print("[warn] 队伍超过 4 人，只取前 4 个")
        ids = ids[:4]

    out = [f"# 输出轴：{' / '.join(ds.operators[i].name_zh or i for i in ids)}", ""]
    for mode, label in (("cold", "冷启动轴（终结技能量 0）"),
                        ("hot", "热启动轴（终结技能量满）")):
        if args.mode not in (mode, "both"):
            continue
        sc = Scenario(team=ids, start_energy=mode, duration_s=args.duration,
                      level=args.level, skill_level=args.skill_level)
        notes = lo.apply_presets(ds, sc, equip=args.equip,
                                 weapon=args.weapon, equip_level=args.equip_level)
        rr = search_rotation(ds, sc, beam=args.beam, steps=args.steps,
                             verbose=args.verbose)
        if notes:
            print(f"[{label}] 预设：{' · '.join(notes)}")
        print(f"[{label}] 伤害 {rr.result.total_damage:,.0f} / DPS {rr.result.dps:,.0f}"
              f" / 合法 {rr.result.legality_ok}")
        print("   " + explain.explain_rotation(rr.result, ds))
        out.append(explain.axis_markdown(rr.result, ds, label))
        out.append("")
    text = "\n".join(out)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[axis] 报告写入 {args.out}")
    if not args.out:
        print(text[:4000])
    return 0


def cmd_team(args) -> int:
    from .report import explain
    from .search.teams import TeamQuery, recommend

    ds = _load_dataset()
    if ds is None:
        return 1
    q = TeamQuery(
        must=[x.strip() for x in (args.must or "").split(",") if x.strip()],
        ban=[x.strip() for x in (args.ban or "").split(",") if x.strip()],
        pool=[x.strip() for x in (args.pool or "").split(",") if x.strip()],
        size=args.size, main=args.main, top_k=args.top,
        evaluate=args.evaluate, duration_s=args.duration,
    )
    res = recommend(ds, q, level=args.level, skill_level=args.skill_level)
    if res["missing_names"]:
        print(f"[warn] 未识别的名字：{res['missing_names']}")

    lines = ["# 配队推荐", "",
             f"- 必须包含：{q.must or '无'}；禁止：{q.ban or '无'}；"
             f"评估了 {res['evaluated']} 队",
             ""]
    for i, t in enumerate(res["top"], 1):
        cold, hot = t["cold"], t["hot"]
        lines.append(f"## {i}. {' / '.join(t['names'])}  （评分 {t['score']:,.0f}）")
        lines.append("")
        lines.append(f"- 冷启动：{cold['result']['total_damage']:,.0f} 伤害 / "
                     f"DPS {cold['result']['dps']:,.0f}")
        lines.append(f"- 热启动：{hot['result']['total_damage']:,.0f} 伤害 / "
                     f"DPS {hot['result']['dps']:,.0f}")
        for r in t["reason"]:
            lines.append(f"- {r}")
        lines.append("")
        lines.append(explain.explain_rotation(_res_obj(cold), ds))
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[team] 报告写入 {args.out}")
    else:
        print(text)

    if args.json:
        import json as _json
        from pathlib import Path
        Path(args.json).write_text(
            _json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[team] JSON 写入 {args.json}")
    return 0


def _res_obj(d: dict):
    from .engine.battle import Result
    return Result(**d["result"])


def cmd_explain(args) -> int:
    ds = _load_dataset()
    if ds is None:
        return 1
    op = ds.resolve(args.operator)
    if op is None:
        print(f"[error] 未知干员：{args.operator}")
        return 2
    print(f"# {op.name_zh}（{op.char_id}）")
    print(f"- {op.rarity}★ {op.profession} / {op.element} / {op.weapon_type}")
    print(f"- 主/副属性：{op.main_attr} / {op.sub_attr}")
    print(f"- TAG：{'、'.join(op.battle_tags)}")
    for key, sk in op.skills.items():
        t = sk.timing
        sem = sk.semantics or {}
        print(f"  - {config.SLOT_ZH.get(sk.slot, sk.slot):<4s} {sk.name_zh or sk.skill_id}")
        print(f"      帧：承诺 {t.commit_frames}f / 总 {t.duration_frames}f"
              f" / 命中 {t.hit_frames[:6]}")
        print(f"      语义：trigger={sem.get('trigger')} abnormal={sem.get('abnormal')}"
              f" 受击依赖={sem.get('needs_enemy_hit')}")
        if sk.wiki_anchor:
            print(f"      wiki 锚：{sk.wiki_anchor.get('field')}")
    return 0


def cmd_update(args) -> int:
    """一条命令更新游戏数据：sync → build → 数据自检。

    适用于"游戏出了新版本/新干员/新装备"之后：
      python run.py update
    失败不覆盖：sync 失败会直接停止，保留上一版数据集。
    """
    from argparse import Namespace

    print("[update] 1/2 同步数据源…")
    rc = cmd_sync(Namespace(force=False, strict=False))
    if rc != 0:
        print("[update] 同步失败，保留现有数据集")
        return rc
    print("[update] 2/2 重建数据集…")
    rc = cmd_build(Namespace(force=False, no_semantics=False, llm=False))
    if rc != 0:
        return rc
    print("[update] 完成。报告见 data/reports/（coverage / data-quality / drift）")
    return 0


def cmd_check(args) -> int:
    p = config.NORM / "dataset.json"
    if not p.exists():
        print("[check] 还没有数据集，先运行 build")
        return 1
    dataset = json.loads(p.read_text(encoding="utf-8"))
    manifest = json.loads((config.NORM / "manifest.json").read_text(encoding="utf-8"))
    cov = dataset.get("coverage") or {}
    print(f"[check] version={dataset['version']} operators={cov.get('operators')}")
    print(f"        帧数据齐 {cov.get('with_full_timing')} / 倍率齐 {cov.get('with_multipliers')}"
          f" / wiki 文本 {cov.get('with_wiki_text')}")
    warn = 0
    for key in ("missing_normal_chain", "missing_power_attack", "missing_battle_skill"):
        vals = cov.get(key) or []
        if vals:
            warn += len(vals)
            print(f"  ! {key}: {', '.join(vals)}")
    for cid in dataset.get("operator_ids", []):
        op = json.loads((config.NORM / "operators" / f"{cid}.json").read_text(encoding="utf-8"))
        if not op.get("name_zh"):
            print(f"  ! {cid} 缺中文名")
            warn += 1
        for sk in (op.get("skills") or {}).values():
            if sk["timing"].get("duration_frames") is None:
                print(f"  ! {cid}/{sk['skill_id']} 缺帧数据")
                warn += 1
    cp = cov_mod.write_constants()
    print(f"[check] 常量来源表：{cp}")
    qp = None
    try:
        from .data import quality
        from .model import Dataset
        qp = quality.write_report(Dataset.load(config.NORM))
        print(f"[check] 数据自检：{qp.name}（元素来源 / 兜底项 / 未建模属性）")
    except Exception as e:  # noqa: BLE001
        print(f"[check] 数据自检跳过：{type(e).__name__}")
    print(f"[check] 警告 {warn} 条；报告见 data/reports/")
    return 0 if warn == 0 or not args.strict else 3




def cmd_serve(args) -> int:
    from .web import server
    # 首启没有数据集时不再直接退出：后台构建，页面显示进度
    server.serve(port=args.port, open_browser=not args.no_browser,
                 auto_build=True)
    return 0



def cmd_mechanics(args) -> int:
    from .data import mechanics
    p = mechanics.write_report()
    print(f"[mechanics] 报告写入 {p}")
    return 0



def cmd_loadout(args) -> int:
    from .search import loadout
    ds = _load_dataset()
    if ds is None:
        return 1
    ids = []
    for n in args.team.split(","):
        op = ds.resolve(n.strip())
        if op is None:
            print(f"[warn] 未知干员：{n}")
            continue
        ids.append(op.char_id)
    if not ids:
        return 2
    rec = loadout.recommend_for_team(ds, ids, args.weapons, args.sets)
    lines = ["# 装备与武器推荐", ""]
    for cid, row in rec.items():
        lines.append(f"## {row['name']}")
        lines.append("")
        lines.append("| 推荐武器 | 稀有度 | ATK | ATK% | 主属性 | 等效ATK |")
        lines.append("|---|---|---|---|---|---|")
        for w in row["weapons"]:
            lines.append(f"| `{w['weapon_id']}` | {w['rarity']}★ | {w['atk']:.0f} | "
                         f"{w['atk_pct']:.0%} | +{w['mainattr']:.0f} | {w['score']:.0f} |")
        lines.append("")
        for s in row["equipment"]:
            lines.append(f"- **{s['label']}**（精锻 {s['enhance_level']}）："
                         + "、".join(f"`{x}`" for x in s["pieces"]))
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[loadout] 写入 {args.out}")
    else:
        print(text)
    return 0

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="endfield", description="终末地配队模拟")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="拉取数据源")
    p.add_argument("--force", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("build", help="构建归一化数据集")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-semantics", action="store_true")
    p.add_argument("--llm", action="store_true", help="启用 LLM 语义增强（需 API key）")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("semantics", help="重新抽取技能语义")
    p.add_argument("--llm", action="store_true")
    p.set_defaults(func=cmd_semantics)

    p = sub.add_parser("check", help="检查覆盖率/完整性")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("update", help="一条命令更新数据（sync → build → 自检）")
    p.set_defaults(func=cmd_update)


    p = sub.add_parser("axis", help="单队输出轴（冷/热启动）")
    p.add_argument("--team", required=True, help="逗号分隔，2~4 人")
    p.add_argument("--mode", choices=["cold", "hot", "both"], default="both")
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--level", type=int, default=90)
    p.add_argument("--skill-level", type=int, default=12)
    p.add_argument("--equip", choices=["none", "output", "sustain"],
                   default="none", help="装备预设（默认不穿）")
    p.add_argument("--equip-level", type=int, default=3, help="精锻等级 0~3")
    p.add_argument("--weapon", type=int, default=0,
                   help="武器预设：0=默认，1~3=推荐顺位")
    p.add_argument("--beam", type=int, default=3)
    p.add_argument("--steps", type=int, default=26)
    p.add_argument("--out", default=None)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_axis)

    p = sub.add_parser("team", help="按约束推荐配队")
    p.add_argument("--must", default="", help="必须包含（逗号分隔）")
    p.add_argument("--ban", default="", help="禁止包含")
    p.add_argument("--pool", default="", help="可选池（默认全部）")
    p.add_argument("--size", type=int, default=4)
    p.add_argument("--main", default=None)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--evaluate", type=int, default=12)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--level", type=int, default=90)
    p.add_argument("--skill-level", type=int, default=12)
    p.add_argument("--out", default=None)
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_team)

    p = sub.add_parser("explain", help="查看单干员数据/语义")
    p.add_argument("operator")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("mechanics", help="从数据表导出机制原文与数值")
    p.set_defaults(func=cmd_mechanics)

    p = sub.add_parser("loadout", help="推荐武器与两套装备")
    p.add_argument("--team", required=True)
    p.add_argument("--weapons", type=int, default=3)
    p.add_argument("--sets", type=int, default=2)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_loadout)

    p = sub.add_parser("serve", help="启动本地 UI（浏览器/桌面壳）")
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
