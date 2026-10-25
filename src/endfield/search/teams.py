"""配队搜索：约束 → 候选队 → 逐队跑轴 → 排序。

约束来自用户：
  must  （必须包含）
  ban   （禁止包含）
  pool  （可选池，默认全部）
  size  （默认 4）
  main  （指定主控，可选）
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .. import config
from ..model import Dataset, Operator
from ..engine.battle import Scenario
from .rotations import search_pair


@dataclass
class TeamQuery:
    must: list[str] = field(default_factory=list)
    ban: list[str] = field(default_factory=list)
    pool: list[str] = field(default_factory=list)
    size: int = 4
    main: str | None = None
    top_k: int = 5
    evaluate: int = 12
    duration_s: float = 60.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TeamResult:
    team: list[str]
    names: list[str]
    cold: dict
    hot: dict
    score: float
    reason: list[str]


def _resolve(ds: Dataset, names: list[str]) -> tuple[list[str], list[str]]:
    ids, missing = [], []
    for n in names:
        ops = ds.resolve_all(n)
        if not ops:
            missing.append(n)
        else:
            ids.append(ops[0].char_id)
    return ids, missing


def _resolve_all(ds: Dataset, names: list[str]) -> list[str]:
    ids: list[str] = []
    for n in names:
        for op in ds.resolve_all(n):
            if op.char_id not in ids:
                ids.append(op.char_id)
    return ids


def pool_of(ds: Dataset, q: TeamQuery) -> tuple[list[Operator], list[str]]:
    base = [o for o in ds.operators.values()
            if any(s.slot == "battle_skill" for s in o.skills.values())]
    if q.pool:
        ids, missing = _resolve(ds, q.pool)
        base = [ds.operators[i] for i in ids]
    must_ids, miss1 = _resolve(ds, q.must)
    ban_ids, miss2 = _resolve_all(ds, q.ban), []
    ids_ban = set(_resolve_all(ds, q.ban))
    base = [o for o in base if o.char_id not in ids_ban]
    # 必须包含的先放进来
    for cid in must_ids:
        if cid not in {o.char_id for o in base}:
            if cid in ds.operators:
                base.append(ds.operators[cid])
    return base, miss1 + miss2


def _power(op: Operator, level: int, skill_level: int) -> float:
    """静态强度粗估：单位承诺时间的伤害能力 + 功能分。"""
    atk = op.stat_at(level).get("atk", 0.0)
    best = 0.0
    for sk in op.skills.values():
        if sk.slot in ("battle_skill", "ultimate", "combo_skill"):
            commit = max(1, sk.timing.commit_frames or 30)
            mult = sk.total_multiplier(skill_level)
            best = max(best, mult * atk / commit)
    sem = op.semantics or {}
    bonus = 0.0
    if sem.get("has_hit_dependent_skill"):
        bonus += 0.05
    return best * (1.0 + bonus)


def static_rank(ops: list[Operator], level: int, skill_level: int) -> list[Operator]:
    return sorted(ops, key=lambda o: _power(o, level, skill_level), reverse=True)


def candidate_teams(ds: Dataset, q: TeamQuery, level: int = 80,
                    skill_level: int = 7) -> tuple[list[list[Operator]], list[str]]:
    pool, missing = pool_of(ds, q)
    must_ids, _ = _resolve(ds, q.must)
    must_ops = [ds.operators[c] for c in must_ids if c in ds.operators]
    rest = [o for o in pool if o.char_id not in {m.char_id for m in must_ops}]
    rest = static_rank(rest, level, skill_level)

    need = max(0, q.size - len(must_ops))
    if need == 0:
        return [must_ops[:q.size]], missing
    # 用"元素/角色互补"做剪枝，只取前 N 个补位选手
    seen_ids = {m.char_id for m in must_ops}
    rest = [o for o in rest if o.char_id not in seen_ids and o.char_id not in {
        c for c in _resolve_all(ds, q.ban)}]
    shortlist = rest[:max(need, min(len(rest), q.evaluate + 4))]
    combos = list(itertools.combinations(shortlist, need))
    teams = [must_ops + list(c) for c in combos]
    return teams, missing


def evaluate_team(ds: Dataset, team: list[Operator], q: TeamQuery,
                  level: int = 80, skill_level: int = 7) -> TeamResult | None:
    ids = [o.char_id for o in team]
    names = [o.name_zh or o.char_id for o in team]
    base = dict(level=level, skill_level=skill_level, duration_s=q.duration_s,
                main=q.main or ids[0])
    try:
        cold, hot = search_pair(
            ds,
            Scenario(team=ids, start_energy="cold", **base),
            Scenario(team=ids, start_energy="hot", **base))
    except Exception:  # noqa: BLE001
        return None
    score = 0.5 * cold.result.dps + 0.5 * hot.result.dps
    reasons = _reasons(ds, team, cold, hot)
    return TeamResult(team=ids, names=names,
                      cold=cold.to_dict(), hot=hot.to_dict(),
                      score=round(score, 2), reason=reasons)


def _reasons(ds: Dataset, team: list[Operator], cold, hot) -> list[str]:
    out = []
    elems = {o.element for o in team if o.element}
    out.append(f"元素覆盖：{'/'.join(sorted(elems))}")
    for o in team:
        trg = (o.semantics or {}).get("combo_trigger")
        if trg and trg != "none":
            out.append(f"{o.name_zh} 连携触发：{trg}")
    if cold.result.legality_ok:
        out.append("冷启动轴合法性通过")
    else:
        out.append("冷启动轴存在合法性问题：" + "; ".join(cold.result.legality_issues[:3]))
    if hot.result.dps > cold.result.dps * 1.15:
        out.append("热启动收益显著（终结技前置）")
    return out


def recommend(ds: Dataset, q: TeamQuery, level: int = 80,
              skill_level: int = 7, verbose: bool = True) -> dict:
    teams, missing = candidate_teams(ds, q, level, skill_level)
    if verbose:
        print(f"[team] 候选队 {len(teams)} 个，评估前 {min(q.evaluate, len(teams))} 个")
    results: list[TeamResult] = []
    seen = set()
    for team in teams[:q.evaluate]:
        key = tuple(sorted(o.char_id for o in team))
        if key in seen:
            continue
        seen.add(key)
        r = evaluate_team(ds, team, q, level, skill_level)
        if r:
            results.append(r)
    results.sort(key=lambda r: r.score, reverse=True)
    return {
        "query": q.to_dict(),
        "missing_names": missing,
        "top": [r.__dict__ for r in results[:q.top_k]],
        "evaluated": len(results),
    }
