"""后台任务：数据构建 / LLM 复核。

任务只做编排 + 驱动状态机；对外只暴露 start_* / run_*。
"""

from __future__ import annotations

import threading

from .. import config
from .state import STATE, dataset_ok


def _build_report(frac: float, msg: str) -> None:
    # build 的 0~1 映射到整体 0~90%（后面还有语义层与报告）
    STATE.build_progress(int(max(0.0, min(1.0, frac)) * 90), msg)


def run_build(verbose: bool = False) -> None:
    """同步构建（失败写进状态机，不抛给调用方）。"""
    if not STATE.begin_build():
        return
    try:
        if verbose:
            print("[serve] 首次运行：正在从数据源构建数据集（需要联网，约 1~3 分钟）…")
        from ..data import build as build_mod
        build_mod.build(verbose=verbose, progress=_build_report)

        STATE.build_progress(92, "抽取技能语义…")
        try:
            from ..semantics import apply as sem
            from ..semantics import llm_config
            cfg = llm_config.load()
            ops = sem.load_operators()
            stats = sem.apply_to_operators(
                ops, use_llm=llm_config.available(cfg), cfg=cfg, verbose=verbose)
            sem.save_operators(ops)
            sem.write_semantics_report(ops, stats)
            if stats.get("llm_enabled"):
                sem.write_llm_review(ops, stats, cfg)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[serve] 语义层跳过：{type(e).__name__}")

        STATE.build_progress(97, "生成机制与常量报告…")
        try:
            from ..data import mechanics
            mechanics.write_report()
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[serve] mechanics 报告跳过：{type(e).__name__}")

        STATE.invalidate()
        ds = STATE.dataset()              # 校验真的能加载
        STATE.build_done(ds.version)
        if verbose:
            print(f"[serve] 数据就绪：{len(ds.operators)} 名干员，version={ds.version}")
    except Exception as e:  # noqa: BLE001
        STATE.build_failed(f"{type(e).__name__}: {e}")
        if verbose:
            print(f"[serve] 构建失败：{type(e).__name__}: {e}")


def start_build(verbose: bool = False) -> None:
    if STATE.data.state == "building":
        return
    threading.Thread(target=run_build, kwargs={"verbose": verbose},
                     daemon=True, name="endfield-build").start()


def _public_stats(stats: dict) -> dict:
    keep = ("skills", "known_trigger", "unknown_trigger", "unknown_rate",
            "hit_dependent", "llm_used", "llm_rejected", "llm_cache",
            "llm_calls", "repaired", "prompt_tokens", "completion_tokens",
            "cost", "mode", "prompt_version")
    out = {k: stats.get(k) for k in keep}
    out["changed_skills"] = len(stats.get("changes") or [])
    out["conflicts"] = len(stats.get("conflicts") or [])
    out["changes"] = (stats.get("changes") or [])[:40]
    out["conflict_items"] = (stats.get("conflicts") or [])[:20]
    return out


def _llm_worker(mode: str, limit: int | None) -> None:
    try:
        from ..semantics import apply as sem_apply
        from ..semantics import llm_config
        cfg = llm_config.load()
        if not llm_config.available(cfg):
            raise RuntimeError("LLM 未配置（需要启用 + API key）")
        ops = sem_apply.load_operators()
        if not ops:
            raise RuntimeError("没有数据集，先构建")
        STATE.llm_progress(f"复核 {len(ops)} 名干员的技能…")
        stats = sem_apply.apply_to_operators(
            ops, use_llm=True, mode=mode, limit=limit, cfg=cfg, verbose=False)
        sem_apply.save_operators(ops)
        sem_apply.write_semantics_report(ops, stats)
        sem_apply.write_llm_review(ops, stats, cfg)
        STATE.llm_done(_public_stats(stats))
    except Exception as e:  # noqa: BLE001
        STATE.llm_failed(f"{type(e).__name__}: {e}")


def run_llm(mode: str = "gaps", limit: int | None = None) -> None:
    """同步复核（状态机转移由 begin_llm 保证）。"""
    try:
        STATE.begin_llm()
    except RuntimeError as e:
        STATE.llm_failed(str(e))
        return
    _llm_worker(mode, limit)


def start_llm(mode: str = "gaps", limit: int | None = None) -> dict:
    if STATE.llm.state == "running":
        return STATE.llm.as_dict(ready_state="done")
    STATE.begin_llm()                     # 前置条件不满足会抛 RuntimeError
    threading.Thread(target=_llm_worker, kwargs={"mode": mode, "limit": limit},
                     daemon=True, name="endfield-llm").start()
    return STATE.llm.as_dict(ready_state="done")


def boot(auto_build: bool = True, verbose: bool = True) -> None:
    """启动时对齐状态：已有数据 → ready；否则后台构建。"""
    if dataset_ok(config.NORM / "dataset.json"):
        try:
            ds = STATE.dataset()
            STATE.mark_ready(ds.version)
        except Exception as e:  # noqa: BLE001
            STATE.build_failed(f"{type(e).__name__}: {e}")
    elif auto_build:
        start_build(verbose=verbose)
