"""帧级战斗仿真。

模型（全部对应 docs/02-计划.md §2.3 的决策表）：
  * 时间 = 整数帧，30 fps。
  * 一次施放 = 承诺 exclusiveFrame 帧（期间该角色不能行动），
    之后可被下一个动作取消；不取消则跑到 durationFrame。
  * 命中按 SkillData 的 DamageAction 帧落在未来，由 advance_to() 统一结算。
  * 技力全队共享；终结技能量各自独立。
  * 连携技需要"窗口"：由重击 / 受击 / 失衡 / 状态事件打开，持续 COMBO_WINDOW_FRAMES。
  * 敌人按固定间隔出手（场景参数），命中主控即视为一次受击事件。

已知简化（会在 Result.warnings 里如实列出）：
  * 不建模 buff/debuff 体系、暴击逐次随机（用期望值）、元素异常伤害。
  * 多段技能的"分支"（if/else）无法区分，倍率可能偏高。
"""

from __future__ import annotations

import copy
import heapq
from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..model import Dataset, Operator
from . import damage as dmgmod
from . import reactions as rxmod


# ---------------------------------------------------------------- 输入

@dataclass
class Scenario:
    team: list[str]
    duration_s: float = 60.0
    level: int = 80
    skill_level: int = 7
    enemy_def: float = 100.0
    enemy_res: float = 0.0
    enemy_poise: float = 100.0
    enemy_hp: float = 1e12
    attack_interval_frames: int = 240
    start_energy: str = "cold"          # cold | hot
    weapon_atk: float = 0.0        # 额外的武器 ATK（覆盖用）
    weapon_level: int = 80          # 默认武器等级
    main: str | None = None
    seed: int = 0
    # 主控以外的干员会自动普攻（游戏内教学页 wiki_tut_bat_team_1）
    offfield_auto_attack: bool = True

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)


@dataclass
class Action:
    kind: str                  # attack / skill / combo / ultimate / wait
    op: str | None = None

    def key(self) -> tuple:
        return (self.kind, self.op)

    def label(self, ds: Dataset | None = None) -> str:
        if self.kind == "wait":
            return "等待"
        name = self.op
        if ds is not None and self.op:
            o = ds.operators.get(self.op)
            if o and o.name_zh:
                name = o.name_zh
        zh = {"attack": "普攻", "skill": "战技", "combo": "连携技",
              "ultimate": "终结技"}.get(self.kind, self.kind)
        return f"{name}·{zh}"


@dataclass
class Result:
    scenario: dict
    total_damage: float
    duration_frames: int
    dps: float
    per_operator: dict[str, float]
    timeline: list[dict]
    warnings: list[str]
    unmodeled: list[str]
    legality_ok: bool
    legality_issues: list[str]
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.__dict__)


# ---------------------------------------------------------------- 战斗

MAX_COMMIT_FRAMES = 120     # 单动作承诺帧上限（4s），用于兜底数据异常


class Battle:
    """单场战斗的可克隆状态机。"""

    def __init__(self, ds: Dataset, sc: Scenario):
        self.ds = ds
        self.sc = sc
        self.ids: list[str] = list(sc.team)
        self.ops: dict[str, Operator] = {}
        for cid in self.ids:
            op = ds.operators.get(cid)
            if op is None:
                raise KeyError(f"未知干员 {cid}")
            self.ops[cid] = op

        self.t = 0
        self.end = int(sc.duration_s * config.FPS)
        self.sp = float(config.guess("SP_MAX"))
        self.energy: dict[str, float] = {}
        self.ult_cd_until: dict[str, int] = {c: 0 for c in self.ids}
        self.combo_cd_until: dict[str, int] = {c: 0 for c in self.ids}
        self.window_until: dict[str, int] = {c: -1 for c in self.ids}
        self.busy_until: dict[str, int] = {c: 0 for c in self.ids}
        # 全局动作锁：同一时刻只有一个角色在场上行动（游戏机制）
        self.global_busy: int = 0
        # 非主控干员的自动普攻排程
        self.auto_next: dict[str, int] = {c: 18 for c in self.ids}
        self.stage: dict[str, int] = {c: 0 for c in self.ids}
        self.main: str = sc.main or self.ids[0]
        self.enemy_poise: float = sc.enemy_poise
        self.staggered_until: int = -1
        self.next_enemy_attack: int = sc.attack_interval_frames
        self.total_damage: float = 0.0
        self.per_op: dict[str, float] = {c: 0.0 for c in self.ids}
        self.pending: list[dict] = []
        self.rx = rxmod.ReactionState()
        self.log: list[dict] = []
        self.issues: list[str] = []
        self.counters: dict[str, int] = {}

        for cid in self.ids:
            ult = self._skill(cid, "ultimate")
            cap = 0.0
            if ult and ult.levels:
                cap = float(ult.levels[min(len(ult.levels) - 1,
                                           sc.skill_level - 1)].ult_cost or 0.0)
            self.energy[cid] = cap if sc.start_energy == "hot" else 0.0

    # ------------------------------------------------------------ 工具

    def _skill(self, cid: str, slot: str):
        op = self.ops[cid]
        for sk in op.skills.values():
            if sk.slot == slot:
                return sk
        return None

    def _energy_cap(self, cid: str) -> float:
        ult = self._skill(cid, "ultimate")
        if not ult or not ult.levels:
            return 100.0
        lv = min(len(ult.levels), max(1, self.sc.skill_level)) - 1
        return float(ult.levels[lv].ult_cost or 100.0)

    def _atk(self, cid: str) -> float:
        op = self.ops[cid]
        row = op.stat_at(self.sc.level)
        main_v = row.get("str" if op.main_attr == "力量" else
                         "agi" if op.main_attr == "敏捷" else
                         "wisd" if op.main_attr == "智识" else "will", 0)
        sub_v = row.get("str" if op.sub_attr == "力量" else
                        "agi" if op.sub_attr == "敏捷" else
                        "wisd" if op.sub_attr == "智识" else "will", 0)
        w_atk = self.sc.weapon_atk
        if not w_atk:
            curve = (op.weapon or {}).get("atk_by_level") or []
            if curve:
                idx = max(0, min(int(self.sc.weapon_level), len(curve)) - 1)
                w_atk = float(curve[idx])
        return dmgmod.effective_atk(row.get("atk", 0.0), main_v or 0, sub_v or 0,
                                    w_atk)

    def _lv_index(self, sk) -> int:
        return min(len(sk.levels), max(1, self.sc.skill_level)) - 1

    def _open_window(self, cid: str, trigger: str, frame: int) -> None:
        sk = self._skill(cid, "combo_skill")
        if not sk:
            return
        sem = sk.semantics or {}
        if sem.get("trigger") != trigger:
            return
        if self.combo_cd_until[cid] > frame:
            return
        self.window_until[cid] = max(self.window_until[cid],
                                     frame + int(config.guess("COMBO_WINDOW_FRAMES")))

    def _open_windows_for(self, trigger: str, frame: int) -> None:
        for cid in self.ids:
            self._open_window(cid, trigger, frame)

    # ------------------------------------------------------------ 时间

    def advance_to(self, target: int) -> None:
        """推进到 target 帧（不含），按帧序结算命中 / 自动普攻 / 敌人出手。"""
        target = max(target, self.t)
        self.pending.sort(key=lambda x: x["frame"])
        guard = 0
        while guard < 20000:
            guard += 1
            nxt_hit = self.pending[0]["frame"] if self.pending else None
            nxt_auto = self._next_auto_frame()
            cands = [f for f in (nxt_hit, nxt_auto)
                     if f is not None and f < target]
            if not cands:
                break
            frame = min(cands)
            self._regen(self.t, frame)
            self.t = max(self.t, frame)
            self._enemy_attacks_between(self.t, frame + 1)
            if nxt_hit is not None and nxt_hit <= frame:
                item = self.pending.pop(0)
                item["frame"] = max(frame, self.t)
                self._apply_hit(item)
            else:
                self._do_auto_attacks(max(frame, self.t))
        self._regen(self.t, target)
        self._enemy_attacks_between(self.t, target)
        self.t = max(self.t, target)

    # ------------------------------------------------------------ 主控外自动普攻

    def _next_auto_frame(self) -> int | None:
        if not self.sc.offfield_auto_attack:
            return None
        best = None
        for cid, f in self.auto_next.items():
            if cid == self.main:
                continue
            if best is None or f < best:
                best = f
        return best

    def _do_auto_attacks(self, frame: int) -> None:
        """非主控干员自动普攻（游戏内教学页 wiki_tut_bat_team_1）。"""
        frame = max(frame, self.t)
        for cid in list(self.ids):
            if cid == self.main:
                continue
            if self.auto_next.get(cid, 10**9) > frame:
                continue
            if self.busy_until[cid] > frame:
                # 在放技能，等它结束再继续自动攻击
                self.auto_next[cid] = self.busy_until[cid]
                continue
            op = self.ops[cid]
            chain = op.normal_chain
            idx = self.stage.get(cid, 0)
            if chain and idx < len(chain):
                sk = op.skills.get(chain[idx])
                self.stage[cid] = idx + 1
            else:
                sk = self._skill(cid, "power_attack")
                self.stage[cid] = 0
            if sk is None:
                self.auto_next[cid] = frame + 60
                continue
            commit = min(sk.timing.commit_frames or 30, MAX_COMMIT_FRAMES)
            self._schedule_hits(cid, sk, frame, kind="auto")
            self.auto_next[cid] = frame + max(1, commit) + int(
                config.guess("AUTO_ATTACK_GAP_FRAMES"))
            self.counters["auto_attack"] = self.counters.get("auto_attack", 0) + 1

    def _regen(self, a: int, b: int) -> None:
        if b <= a:
            return
        dt = (b - a) / config.FPS
        self.sp = min(config.guess("SP_MAX"),
                      self.sp + dt * config.guess("SP_REGEN_PER_SEC"))

    def _enemy_attacks_between(self, a: int, b: int) -> None:
        while self.next_enemy_attack < b:
            frame = self.next_enemy_attack
            if frame >= a:
                # 主控受击 → 打开 on_hit_taken 窗口
                self._open_windows_for("on_hit_taken", frame)
                self.log.append({"frame": frame, "kind": "enemy_attack",
                                 "op": None, "label": "敌人攻击（主控受击）",
                                 "damage": 0.0})
            self.next_enemy_attack += self.sc.attack_interval_frames

    def _apply_hit(self, item: dict) -> None:
        cid = item["cid"]
        dmg = item["dmg"]
        poise = item["poise"]
        element = item["element"]
        frame = item["frame"]
        abnormal = item.get("abnormal")

        if dmg > 0:
            self.total_damage += dmg
            self.per_op[cid] = self.per_op.get(cid, 0.0) + dmg

        # ---- 元素 / 物理异常（附加伤害，属于施放者）
        bonus = 0.0
        if item.get("applies_element", True) and element in rxmod.ARTS_ELEMENTS:
            bonus, event = self.rx.apply_element(
                element, self._atk(cid), frame, self.sc.level)
        elif item.get("applies_element", True) and abnormal:
            bonus, event = self.rx.apply_physical(
                abnormal, self._atk(cid), frame, self.sc.level)
        else:
            event = None
        if bonus:
            staged = self.staggered_until > frame
            if staged:
                bonus *= config.guess("STAGGER_DMG_MULT")
            bonus = round(bonus)
            self.total_damage += bonus
            self.per_op[cid] = self.per_op.get(cid, 0.0) + bonus
            self.counters["reaction_dmg"] = (
                self.counters.get("reaction_dmg", 0) + int(bonus))
        if event:
            self.log.append({
                "frame": frame, "kind": "reaction", "op": cid,
                "label": f"{self.ops[cid].name_zh}·{event}",
                "damage": round(bonus, 1),
            })

        if poise > 0:
            self.enemy_poise += poise
            if self.enemy_poise >= self.sc.enemy_poise:
                self.enemy_poise = 0.0
                self.staggered_until = frame + int(10 * config.FPS)
                self._open_windows_for("on_break", frame)
                self.log.append({"frame": frame, "kind": "stagger", "op": None,
                                 "label": "敌人失衡", "damage": 0.0})

    # ------------------------------------------------------------ 合法动作

    def legal_actions(self, include_wait: bool = True) -> list[Action]:
        acts: list[Action] = []
        if self.t >= self.end:
            return acts
        if self.t < self.global_busy:
            return acts if not include_wait else [Action("wait")]
        # 普攻：任何空闲角色都能打（非主控会先付切人开销，见 apply）
        for cid in self.ids:
            if self.busy_until[cid] <= self.t:
                acts.append(Action("attack", cid))
        for cid in self.ids:
            if self.busy_until[cid] > self.t:
                continue
            op = self.ops[cid]
            bs = self._skill(cid, "battle_skill")
            if bs and bs.levels:
                cost = bs.levels[self._lv_index(bs)].sp_cost or 0.0
                if self.sp + 1e-9 >= cost:
                    acts.append(Action("skill", cid))
            if self.window_until[cid] > self.t and self.combo_cd_until[cid] <= self.t:
                acts.append(Action("combo", cid))
            ult = self._skill(cid, "ultimate")
            if (ult and ult.levels
                    and self.energy.get(cid, 0.0) + 1e-9 >= self._energy_cap(cid)
                    and self.ult_cd_until[cid] <= self.t):
                acts.append(Action("ultimate", cid))
        if include_wait:
            acts.append(Action("wait"))
        return acts

    # ------------------------------------------------------------ 执行

    def apply(self, action: Action) -> None:
        if self.t < self.global_busy:
            self.advance_to(self.global_busy)
        if action.kind == "wait":
            nxt = self.next_event_frame()
            self.advance_to(min(max(nxt, self.t + 1), self.end))
            return
        cid = action.op
        assert cid is not None
        if cid != self.main:
            # 切人开销（任何动作都一样）
            self.advance_to(self.t + int(config.guess("SWITCH_FRAMES")))
            self.main = cid
        if action.kind == "attack":
            self._do_attack(cid)
        elif action.kind == "skill":
            self._do_cast(cid, "battle_skill")
        elif action.kind == "combo":
            self._do_cast(cid, "combo_skill")
        elif action.kind == "ultimate":
            self._do_cast(cid, "ultimate")
        else:
            raise ValueError(action.kind)

    def next_event_frame(self) -> int:
        cands = [self.end]
        cands.append(self.global_busy)
        if self.pending:
            cands.append(min(p["frame"] for p in self.pending))
        cands.append(self.next_enemy_attack)
        for v in self.window_until.values():
            if v > self.t:
                cands.append(v)
        for v in list(self.combo_cd_until.values()) + list(self.ult_cd_until.values()):
            if v > self.t:
                cands.append(v)
        nxt = min((c for c in cands if c > self.t), default=self.end)
        return min(nxt, self.end)

    def _do_attack(self, cid: str) -> None:
        op = self.ops[cid]
        chain = op.normal_chain
        idx = self.stage.get(cid, 0)
        if chain and idx < len(chain):
            sid = chain[idx]
            sk = op.skills.get(sid)
            self.stage[cid] = idx + 1
            heavy = (idx + 1) == len(chain)
        else:
            sk = self._skill(cid, "power_attack")
            self.stage[cid] = 0
            heavy = True
        if sk is None:
            self.issues.append(f"{cid} 无可用普攻")
            return
        self._execute(cid, sk, heavy=heavy)

    def _do_cast(self, cid: str, slot: str) -> None:
        sk = self._skill(cid, slot)
        if sk is None or not sk.levels:
            self.issues.append(f"{cid} 无 {slot}")
            return
        lv = sk.levels[self._lv_index(sk)]
        if slot == "battle_skill":
            cost = lv.sp_cost or 0.0
            if self.sp + 1e-9 < cost:
                self.issues.append(f"技力不足仍施放 {cid}/{slot}")
            self.sp = max(0.0, self.sp - cost)
        if slot == "combo_skill":
            tol = int(config.guess("SWITCH_FRAMES"))
            if self.window_until[cid] + tol <= self.t:
                self.issues.append(f"连携窗口未开仍施放 {cid}")
            self.window_until[cid] = -1
            self.combo_cd_until[cid] = self.t + int(round((lv.cooldown_s or 0) * config.FPS))
        if slot == "ultimate":
            if self.energy.get(cid, 0.0) + 1e-9 < self._energy_cap(cid):
                self.issues.append(f"能量不足仍放终结技 {cid}")
            self.energy[cid] = max(0.0, self.energy.get(cid, 0.0) - self._energy_cap(cid))
            self.ult_cd_until[cid] = self.t + int(round((lv.cooldown_s or 0) * config.FPS))
        self._execute(cid, sk, heavy=False, cast_slot=slot)

    def _schedule_hits(self, cid: str, sk, start: int, *,
                       kind: str = "cast") -> list[int]:
        """把一次施放的伤害实例排进 pending，并写一条 log。"""
        op = self.ops[cid]
        lv = sk.levels[self._lv_index(sk)]
        commit = sk.timing.commit_frames or 10
        total = sk.timing.duration_frames or commit
        if commit > MAX_COMMIT_FRAMES:
            commit = MAX_COMMIT_FRAMES
        atk = self._atk(cid)
        mult = sk.total_multiplier(self.sc.skill_level)
        element = sk.element or op.element
        hits = [h for h in (sk.timing.hit_frames or [commit]) if 0 < h <= commit]
        if not hits:
            hits = [max(1, commit // 2)]
        n = max(1, len(hits))
        share = mult / n
        poise_share = float(lv.multipliers.get("poise") or 0.0) / n
        for hf in hits:
            frame = start + int(hf)
            d, _zones = dmgmod.compute(dmgmod.DamageContext(
                atk=atk, multiplier=share, element=element,
                skill_slot=sk.slot,
                staggered=self.staggered_until > frame,
                enemy_def=self.sc.enemy_def, enemy_res=self.sc.enemy_res,
            ))
            self.pending.append({
                "frame": frame, "cid": cid, "dmg": float(d),
                "poise": poise_share, "element": element,
                "slot": sk.slot,
                "abnormal": (sk.semantics or {}).get("abnormal"),
                # 附着/爆发/异常由"技能"触发；普攻/重击/冲刺只做伤害
                "applies_element": sk.slot not in (
                    "normal_attack", "power_attack", "dash_attack"),
            })
        self.log.append({
            "frame": start,
            "frame_end": start + max(commit, total),
            "commit_end": start + commit,
            "kind": kind,
            "slot": sk.slot,
            "op": cid,
            "label": (f"{op.name_zh}·自动{config.SLOT_ZH.get(sk.slot, sk.slot)}"
                      if kind == "auto"
                      else f"{op.name_zh}·{config.SLOT_ZH.get(sk.slot, sk.slot)}"),
            "damage": 0.0,
            "hits": [start + int(h) for h in hits],
        })
        return hits

    def _execute(self, cid: str, sk, *, heavy: bool, cast_slot: str | None = None) -> None:
        lv = sk.levels[self._lv_index(sk)]
        start = self.t
        commit = sk.timing.commit_frames or 10
        # 数据异常兜底：少数技能的 exclusiveFrame 明显不合理（如 wulfa_attack4 = 239 帧），
        # 会让一次普攻占掉 8 秒。钳制到 120 帧并记录，结果里会给出提示。
        if commit > MAX_COMMIT_FRAMES:
            commit = MAX_COMMIT_FRAMES
            self.counters["commit_clamped"] = self.counters.get("commit_clamped", 0) + 1
        self.busy_until[cid] = start + max(1, commit)
        self.global_busy = start + max(1, commit)
        self._schedule_hits(cid, sk, start, kind="cast")

        # 能量：
        #   战技 → 全队 +6.5（BuffData buff_common_obtain_ultimate_sp 的 usp_everyone）
        #   连携技 → 自身 +技能黑板 usp（没有则用兜底）
        #   终结技 → 消耗
        if sk.slot == "battle_skill":
            team_gain = float(config.guess("ULT_GAIN_BATTLE_SKILL"))
            if team_gain:
                for other in self.ids:
                    self.energy[other] = min(
                        self._energy_cap(other),
                        self.energy.get(other, 0.0) + team_gain)
            self_gain = float(lv.energy_gain or 0.0)
            if self_gain:
                self.energy[cid] = min(self._energy_cap(cid),
                                       self.energy.get(cid, 0.0) + self_gain)
        elif sk.slot == "combo_skill":
            gain = lv.energy_gain
            if gain is None:
                gain = config.guess("ULT_GAIN_COMBO")
            if gain:
                self.energy[cid] = min(self._energy_cap(cid),
                                       self.energy.get(cid, 0.0) + float(gain))
        if heavy:
            self.sp = min(config.guess("SP_MAX"),
                          self.sp + config.guess("SP_GAIN_HEAVY"))
            self._open_windows_for("on_heavy_attack", start + commit)
        if sk.slot == "battle_skill":
            self._open_windows_for("on_skill_hit", start + commit)
        sem = sk.semantics or {}
        if sem.get("abnormal"):
            self._open_windows_for("on_status", start + commit)
        if sem.get("heals"):
            self._open_windows_for("on_heal", start + commit)

        self.counters[sk.slot] = self.counters.get(sk.slot, 0) + 1

    # ------------------------------------------------------------ 输出

    def clone(self) -> "Battle":
        b = Battle.__new__(Battle)
        b.__dict__.update(self.__dict__)
        for name in ("energy", "ult_cd_until", "combo_cd_until", "window_until",
                     "busy_until", "stage", "per_op", "auto_next"):
            setattr(b, name, dict(getattr(self, name)))
        b.pending = [dict(x) for x in self.pending]
        b.rx = self.rx.clone()
        b.log = list(self.log)
        b.issues = list(self.issues)
        b.counters = dict(self.counters)
        return b

    def result(self) -> Result:
        self.advance_to(self.end)
        dur = max(1, self.t) / config.FPS
        warnings = []
        if any(sk.timing.estimated
               for op in self.ops.values() for sk in op.skills.values()):
            warnings.append("部分技能命中帧为估计值（SkillData 未提供 DamageAction）")
        if self.sc.start_energy == "hot":
            warnings.append("热启动：假设全队终结技能量已满")
        if self.counters.get("commit_clamped"):
            warnings.append(
                f"{self.counters['commit_clamped']} 次动作的承诺帧超过 "
                f"{MAX_COMMIT_FRAMES} 帧，已钳制（源数据 exclusiveFrame 异常）")
        unmodeled = [
            "武器/装备加成", "buff/debuff 体系", "元素异常伤害（燃烧/导电/冻结/腐蚀）",
            "物理异常伤害（击飞/倒地/猛击/碎甲）", "暴击随机性（用期望值）",
            "敌人受击硬直与实际出手节奏", "连携技的额外条件（如层数、消耗）",
        ]
        warnings.append("技力恢复速度、切人开销等为 estimate，可在 config.py 校准")
        return Result(
            scenario=self.sc.to_dict(),
            total_damage=round(self.total_damage, 1),
            duration_frames=self.t,
            dps=round(self.total_damage / dur, 2),
            per_operator={c: round(v, 1) for c, v in self.per_op.items()},
            timeline=self.log,
            warnings=warnings,
            unmodeled=unmodeled,
            legality_ok=not self.issues,
            legality_issues=list(self.issues),
            stats={"cast_counts": dict(self.counters),
                   "sp_left": round(self.sp, 1),
                   "energy_left": {c: round(v, 1) for c, v in self.energy.items()},
                   "reactions": self.rx.to_dict()},
        )
