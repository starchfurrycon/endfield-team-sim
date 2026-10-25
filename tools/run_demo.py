#!/usr/bin/env python
"""一键演示：跑通 build → 语义 → 配队 → 冷/热轴，产出报告。

    python tools/run_demo.py
    python tools/run_demo.py --must 陈千语 --duration 40
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from endfield import config                                   # noqa: E402
from endfield.model import Dataset                            # noqa: E402
from endfield.report import explain                            # noqa: E402
from endfield.search.teams import TeamQuery, recommend         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--must", default="陈千语")
    ap.add_argument("--ban", default="")
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--evaluate", type=int, default=8)
    ap.add_argument("--out", default=str(config.REPORTS / "demo.md"))
    args = ap.parse_args()

    if not (config.NORM / "dataset.json").exists():
        print("没有数据集，先运行：python run.py build")
        return 1

    ds = Dataset.load(config.NORM)
    q = TeamQuery(
        must=[x.strip() for x in args.must.split(",") if x.strip()],
        ban=[x.strip() for x in args.ban.split(",") if x.strip()],
        top_k=args.top, evaluate=args.evaluate, duration_s=args.duration)
    res = recommend(ds, q, verbose=True)

    lines = ["# 终末地配队推荐 · 演示报告", "",
             f"- 数据集版本：`{ds.version}`",
             f"- 约束：必须包含 {q.must or '无'} / 禁止 {q.ban or '无'}",
             f"- 战斗窗口：{args.duration:.0f}s", ""]

    for i, t in enumerate(res["top"], 1):
        cold, hot = t["cold"], t["hot"]
        lines.append(f"## {i}. {' / '.join(t['names'])}（评分 {t['score']:,.0f}）")
        lines.append("")
        lines.append(f"- 冷启动 DPS **{cold['result']['dps']:,.0f}**"
                     f"（总伤 {cold['result']['total_damage']:,.0f}）")
        lines.append(f"- 热启动 DPS **{hot['result']['dps']:,.0f}**"
                     f"（总伤 {hot['result']['total_damage']:,.0f}）")
        for r in t["reason"]:
            lines.append(f"- {r}")
        lines.append("")
        lines.append(f"**冷启动打法**：{explain.explain_rotation(_res(cold), ds)}")
        lines.append("")
        lines.append(f"**热启动打法**：{explain.explain_rotation(_res(hot), ds)}")
        lines.append("")
        lines.append(explain.axis_markdown(_res(cold), ds, "冷启动轴"))
        lines.append("")
        lines.append(explain.axis_markdown(_res(hot), ds, "热启动轴"))
        lines.append("")

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[demo] 报告写入 {p}")
    print(f"[demo] Top1: {' / '.join(res['top'][0]['names'])} "
          f"评分 {res['top'][0]['score']:,.0f}")
    return 0


def _res(d: dict):
    from endfield.engine.battle import Result
    return Result(**d["result"])


if __name__ == "__main__":
    sys.exit(main())
