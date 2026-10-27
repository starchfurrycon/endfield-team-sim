"""输出轴搜索（beam + rollout）。

问题定义：给定队伍与场景，在帧级合法性约束下选动作序列，使窗口内总伤害最大。

为什么不是"技能循环表"：动作的承诺帧互不相同，且技力/能量/连携窗口都是跨角色的
耦合资源，最优解依赖具体时序 —— 只能在时间轴上搜。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..model import Dataset
from ..engine.battle import Action, Battle, Result, Scenario


@dataclass
class RotationResult:
    actions: list[Action]
    result: Result
    score: float

    def to_dict(self) -> dict:
        return {
            "actions": [{"kind": a.kind, "op": a.op} for a in self.actions],
            "score": self.score,
            "result": self.result.to_dict(),
        }


_SLOT_TO_KIND = {
    "normal_attack": "attack", "power_attack": "attack",
    "dash_attack": "attack", "plunging_attack": "attack",
    "battle_skill": "skill", "combo_skill": "combo", "ultimate": "ultimate",
}


def actions_from_timeline(timeline: list[dict]) -> list[Action]:
    """把 timeline 还原成完整动作序列（含 rollout 补的部分）。"""
    out: list[Action] = []
    for ev in timeline:
        if ev.get("kind") != "cast":
            continue
        kind = _SLOT_TO_KIND.get(ev.get("slot") or "")
        if kind and ev.get("op"):
            out.append(Action(kind, ev["op"]))
    return out


def _priority(b: Battle, a: Action) -> float:
    """rollout 用的启发式：单位承诺帧的期望伤害。

    为什么不用"技能优先"：终结技的承诺帧很长（100+ 帧），盲目先放会挤掉
    更高性价比的战技。按伤害/帧排序后，热启动自然 ≥ 冷启动。
    """
    if a.kind == "wait" or a.op is None:
        return 0.0
    cid = a.op
    op = b.ops[cid]
    if a.kind == "attack":
        chain = op.normal_chain
        idx = b.stage.get(cid, 0)
        if chain and idx < len(chain):
            sk = op.skills.get(chain[idx])
        else:
            sk = next((s for s in op.skills.values()
                       if s.slot == "power_attack"), None)
    else:
        slot = {"skill": "battle_skill", "combo": "combo_skill",
                "ultimate": "ultimate"}.get(a.kind)
        sk = b._skill(cid, slot) if slot else None
    if sk is None:
        return 0.0
    commit = max(1, sk.timing.commit_frames or 10)
    dmg = b._atk(cid) * sk.total_multiplier(b.sc.skill_level)
    return dmg / commit


def rollout(b: Battle, max_steps: int = 40) -> Battle:
    """贪心跑到底：能放终结技就放，其次连携、战技，否则普攻。"""
    steps = 0
    while b.t < b.end and steps < max_steps:
        acts = [a for a in b.legal_actions(include_wait=False)]
        if not acts:
            nxt = b.next_event_frame()
            if nxt <= b.t:
                break
            b.advance_to(nxt)
            continue
        acts.sort(key=lambda a: _priority(b, a), reverse=True)
        b.apply(acts[0])
        steps += 1
    if b.t < b.end:
        b.advance_to(b.end)
    return b


def _candidates(b: Battle, limit: int) -> list[Action]:
    acts = b.legal_actions(include_wait=False)
    acts.sort(key=lambda a: _priority(b, a), reverse=True)
    # 保证普攻始终在候选里（否则会出现"没人打伤害"的死局）
    out = acts[:limit]
    if not any(a.kind == "attack" for a in out):
        atk = [a for a in acts if a.kind == "attack"]
        if atk:
            out.append(atk[0])
    # 换人是"让另一个主控打重击"的唯一入口，候选里至少留一个
    if not any(a.kind == "switch" for a in out):
        sw = [a for a in acts if a.kind == "switch"]
        if sw:
            out.append(sw[0])
    return out


def dump_ultimates(b: Battle, ds: Dataset) -> list[Action]:
    """热启动的"开场倾泻"：能用就放终结技，优先倍率高的。"""
    used: list[Action] = []
    for _ in range(8):
        if b.t < b.global_busy:
            b.advance_to(b.global_busy)
        cands = [a for a in b.legal_actions(include_wait=False)
                 if a.kind == "ultimate"]
        if not cands:
            break
        def mult(a: Action) -> float:
            op = ds.operators[a.op]
            sk = next((s for s in op.skills.values() if s.slot == "ultimate"), None)
            return sk.total_multiplier(b.sc.skill_level) if sk else 0.0
        cands.sort(key=mult, reverse=True)
        b.apply(cands[0])
        used.append(cands[0])
    return used


def search_rotation(ds: Dataset, sc: Scenario, *, beam: int = 3,
                    steps: int = 26, candidates: int = 6,
                    opening_ults: bool = False,
                    verbose: bool = False) -> RotationResult:
    """搜索一条轴。

    `opening_ults=True` 时**同时**评估"开场倾泻"与"自然攒能"两种策略并取优：
    强行先放终结技只是个启发式起点，不保证更优；而"能量满"本来就包含
    能量 0 的全部可达状态，所以热启动应当不差于冷启动。
    """
    if opening_ults:
        forced = _search_core(ds, sc, True, beam, steps, candidates, verbose)
        natural = _search_core(ds, sc, False, beam, steps, candidates, verbose)
        return forced if forced.score >= natural.score else natural
    return _search_core(ds, sc, False, beam, steps, candidates, verbose)


def _search_core(ds: Dataset, sc: Scenario, dump: bool, beam: int,
                 steps: int, candidates: int,
                 verbose: bool) -> RotationResult:
    root = Battle(ds, sc)
    opening: list[Action] = []
    if dump:
        opening = dump_ultimates(root, ds)
    frontier: list[tuple[Battle, list[Action]]] = [(root, list(opening))]
    best: tuple[float, Battle, list[Action]] = (root.total_damage, root, list(opening))

    for depth in range(steps):
        scored: list[tuple[float, Battle, list[Action]]] = []
        for b, path in frontier:
            if b.t >= b.end:
                scored.append((b.total_damage, b, path))
                continue
            if b.t < b.global_busy:
                b.advance_to(b.global_busy)
            cands = _candidates(b, candidates)
            if not cands:
                nb = b.clone()
                nb.advance_to(nb.next_event_frame())
                scored.append((b.total_damage, nb, path))
                continue
            for a in cands:
                nb = b.clone()
                nb.apply(a)
                rb = rollout(nb)
                scored.append((rb.total_damage, nb, path + [a]))
                if rb.total_damage > best[0]:
                    best = (rb.total_damage, rb, path + [a])
        if not scored:
            break
        scored.sort(key=lambda x: x[0], reverse=True)
        # 去重：同一 (t, sp, 能量概览) 只留一个
        seen = set()
        frontier = []
        for score, b, path in scored:
            key = (b.t, round(b.sp, 1), round(b.total_damage, -1))
            if key in seen:
                continue
            seen.add(key)
            frontier.append((b, path))
            if len(frontier) >= beam:
                break
        if verbose:
            print(f"  depth {depth}: best={best[0]:.0f} frontier={len(frontier)}")
        if all(b.t >= b.end for b, _ in frontier):
            break

    score, b, _path = best
    res = b.result()
    # 用 timeline 还原完整序列：搜索路径只是前缀，后半段是 rollout 补的
    return RotationResult(actions=actions_from_timeline(res.timeline),
                          result=res, score=score)


def axis_from_result(res: Result, ds: Dataset | None = None) -> list[dict]:
    """把 timeline 整理成"轴"：按时间排序的动作表（秒）。"""
    rows = []
    for ev in res.timeline:
        if ev.get("kind") not in ("cast", "switch", "enemy_attack", "stagger"):
            continue
        rows.append({
            "t": round(ev["frame"] / config.FPS, 2),
            "t_end": round(ev.get("commit_end", ev["frame"]) / config.FPS, 2),
            "label": ev.get("label"),
            "kind": ev.get("kind"),
            "hits": [round(h / config.FPS, 2) for h in (ev.get("hits") or [])],
        })
    rows.sort(key=lambda r: (r["t"], r["kind"]))
    return rows

def replay(ds: Dataset, sc: Scenario, actions: list[Action],
           max_steps: int = 60) -> RotationResult:
    """在给定初始条件下重放一条动作序列，再用贪心补满剩余时间。"""
    b = Battle(ds, sc)
    used: list[Action] = []
    for a in actions:
        if b.t >= b.end:
            break
        try:
            b.apply(a)
            used.append(a)
        except Exception:  # noqa: BLE001
            break
    b = rollout(b, max_steps=max_steps)
    return RotationResult(actions=used, result=b.result(), score=b.total_damage)


def search_pair(ds: Dataset, sc_cold: Scenario, sc_hot: Scenario,
                **kw) -> tuple[RotationResult, RotationResult]:
    """冷/热两条轴，并保证「能量满不差于能量 0」。

    做法：冷启动选出的动作序列在热启动条件下**同样合法**（能量只多不少），
    把它重放一遍即可得到热启动的下界，再与搜索结果取优。
    """
    cold = search_rotation(ds, sc_cold, **kw)
    hot = search_rotation(ds, sc_hot, opening_ults=True, **kw)
    seeded = replay(ds, sc_hot, cold.actions)
    best_hot = max((hot, seeded), key=lambda r: r.score)
    return cold, best_hot
