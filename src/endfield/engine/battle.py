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
import json
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
    level: int = 90
    skill_level: int = 12
    enemy_def: float = 100.0
    enemy_res: float = 0.0
    enemy_poise: float = 100.0
    enemy_hp: float = 1e12
    attack_interval_frames: int = 240
    # 敌人节奏模板：slow/normal/fast/burst（对应出手间隔 12/8/5/4 秒）
    enemy_profile: str = "normal"
    start_energy: str = "cold"          # cold | hot
    weapon_atk: float = 0.0        # 额外的武器 ATK（覆盖用）
    weapon_level: int = 90          # 默认武器等级（满级）
    main: str | None = None
    seed: int = 0
    # 主控以外的干员会自动普攻（游戏内教学页 wiki_tut_bat_team_1）
    offfield_auto_attack: bool = True
    potential_level: int = 0      # 潜能等级 0~5（默认不点）
    equip_ids: list[str] = field(default_factory=list)   # 4 件装备（全队同一套，默认空）
    equip_level: int = 3          # 精锻等级 0~3
    # 每角色 4 件装备：优先于 equip_ids（推荐给装是按人分工的，不能全队同一套）
    equip_by_op: dict[str, list[str]] = field(default_factory=dict)
    # 每角色武器覆盖（默认用数据里的默认武器）；用于"换武器对比"
    weapon_by_op: dict[str, str] = field(default_factory=dict)
    weapon_skill_level: int = 9   # 武器技能等级 1~9（被动属性）
    talent_level: int = 3         # 天赋阶段（取数据里可用的最高阶）

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

MAX_COMMIT_FRAMES = 120

# 敌人出手节奏模板（帧）。normal=8s 与 Wiki/实战常见节奏一致。
ENEMY_PROFILES = {"slow": 360, "normal": 240, "fast": 150, "burst": 120}     # 单动作承诺帧上限（4s），用于兜底数据异常


class Battle:
    """单场战斗的可克隆状态机。"""

    def __init__(self, ds: Dataset, sc: Scenario):
        self.ds = ds
        self.sc = sc
        # 属性元数据（id → icon/name）：新属性靠它归类，见 _attr_category
        self.attr_meta: dict = ((ds.constants or {}).get("attr_meta") or {})
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
        interval = ENEMY_PROFILES.get(sc.enemy_profile,
                                      sc.attack_interval_frames)
        # 节奏模板必须对**后续每一次**出手都生效，不能只影响第一次
        self.enemy_interval: int = int(interval)
        self.next_enemy_attack: int = self.enemy_interval
        self.total_damage: float = 0.0
        self.per_op: dict[str, float] = {c: 0.0 for c in self.ids}
        self.pending: list[dict] = []
        self.rx = rxmod.ReactionState()
        self.mod = self._build_mods()
        self._apply_equipment()
        self.log: list[dict] = []
        self.samples: list[dict] = []   # (frame, sp, energy) 水位采样
        self.issues: list[str] = []
        self.counters: dict[str, int] = {}

        for cid in self.ids:
            ult = self._skill(cid, "ultimate")
            cap = 0.0
            if ult and ult.levels:
                cap = float(ult.levels[min(len(ult.levels) - 1,
                                           sc.skill_level - 1)].ult_cost or 0.0)
            self.energy[cid] = cap if sc.start_energy == "hot" else 0.0

        # 水位起点（UI 曲线从 0 帧开始）
        self.samples.append({"f": 0, "sp": round(self.sp, 1),
                             "e": {c: round(v, 1) for c, v in self.energy.items()}})

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

    # ---- 武器：默认取干员数据里的默认武器；weapon_by_op 可逐人换武器
    _WEAPON_CACHE: dict | None = None

    @classmethod
    def _weapon_table(cls) -> dict:
        if Battle._WEAPON_CACHE is None:
            p = config.NORM / "weapons.json"
            try:
                Battle._WEAPON_CACHE = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                Battle._WEAPON_CACHE = {}
        return Battle._WEAPON_CACHE

    def _weapon_row(self, cid: str) -> dict:
        """当前生效的武器数据行（atk_by_level / skill_bb）。

        `weapon_by_op` 里的武器来自 weapons.json（被动是 9 级满基质口径），
        没有覆盖时用干员自带的默认武器，行为与之前完全一致。
        """
        wid = (self.sc.weapon_by_op or {}).get(cid)
        if wid:
            w = self._weapon_table().get(wid)
            if w:
                return {"id": wid,
                        "atk_by_level": w.get("atk_by_level") or [],
                        "skill_bb": [w.get("passive_bb") or {}],
                        "rarity": w.get("rarity"),
                        "name": w.get("name")}
        return self.ops[cid].weapon or {}

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
            curve = (self._weapon_row(cid).get("atk_by_level") or [])
            if curve:
                idx = max(0, min(int(self.sc.weapon_level), len(curve)) - 1)
                w_atk = float(curve[idx])
        extra = (self.mod.get(cid) or {}).get("attrs") or {}
        _ma = float(extra.get("__mainattr__", 0.0))
        if _ma:
            main_v = (main_v or 0) + _ma
            sub_v = (sub_v or 0) + _ma if False else sub_v
        for k in ("str", "agi", "wisd", "will"):
            if k in extra:
                if k == "str":
                    main_v = (main_v or 0) + extra[k] if op.main_attr == "力量" else main_v
                    sub_v = (sub_v or 0) + extra[k] if op.sub_attr == "力量" else sub_v
                elif k == "agi":
                    main_v = (main_v or 0) + extra[k] if op.main_attr == "敏捷" else main_v
                    sub_v = (sub_v or 0) + extra[k] if op.sub_attr == "敏捷" else sub_v
                elif k == "wisd":
                    main_v = (main_v or 0) + extra[k] if op.main_attr == "智识" else main_v
                    sub_v = (sub_v or 0) + extra[k] if op.sub_attr == "智识" else sub_v
                else:
                    main_v = (main_v or 0) + extra[k] if op.main_attr == "意志" else main_v
                    sub_v = (sub_v or 0) + extra[k] if op.sub_attr == "意志" else sub_v
        base = dmgmod.effective_atk(row.get("atk", 0.0), main_v or 0, sub_v or 0,
                                    w_atk)
        base += float((self.mod.get(cid) or {}).get("atk_flat", 0.0))
        return base * (1.0 + float((self.mod.get(cid) or {}).get("atk_pct", 0.0)))

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
            # 敌人出手必须按"旧时间 → 新时间"结算，否则跨事件的出手会被跳过
            self._enemy_attacks_between(self.t, frame + 1)
            self.t = max(self.t, frame)
            if len(self.samples) < 4000:
                self.samples.append({"f": self.t, "sp": round(self.sp, 1),
                                     "e": {c: round(v, 1) for c, v in self.energy.items()}})
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
            self.next_enemy_attack += self.enemy_interval

    def _apply_hit(self, item: dict) -> None:
        cid = item["cid"]
        element = item["element"]
        frame = item["frame"]
        abnormal = item.get("abnormal")

        flat = item.get("flat")
        if flat:
            self.total_damage += flat
            self.per_op[cid] = self.per_op.get(cid, 0.0) + flat
            self.log.append({"frame": frame, "kind": "dot", "op": cid,
                             "label": f"{self.ops[cid].name_zh}·{item.get('label')}",
                             "damage": flat})
            return

        # ---- 基础伤害在"命中瞬间"计算：抗性/易伤/失衡/导电都随状态变化
        if not item.get("no_rx"):
            dmg = self._hit_damage(cid, item["mult"], element, frame,
                                   slot=item.get("slot"))
        else:
            dmg = self._hit_damage(cid, item["mult"], element, frame,
                                   apply_status=False, slot=item.get("slot"))
        if dmg > 0:
            self.total_damage += dmg
            self.per_op[cid] = self.per_op.get(cid, 0.0) + dmg

        # ---- 元素 / 物理异常（附加伤害，属于施放者）
        # 注意：异常伤害只按 ATK / 等级 / AI / 失衡算，不再叠元素伤害词条——
        # 词条口径本身不明确（是否作用于异常），宁可低估不错算。
        if item.get("no_rx"):
            return
        atk = self._atk(cid)
        ai = self._ai(cid)
        bonus = 0.0
        event = None
        followups: list[tuple[int, float, str]] = []
        applies = item.get("applies_element", True)
        if applies and element in rxmod.ARTS_ELEMENTS:
            bonus, event, followups = self.rx.apply_element(
                element, atk, frame, self.sc.level, ai)
        elif applies and abnormal:
            bonus, event = self.rx.apply_physical(
                abnormal, atk, frame, self.sc.level, ai)
            # 冻结时被物理异常击碎 → 碎冰
            if self.rx.is_frozen(frame):
                sd, sev = self.rx.consume_frozen(frame, atk, self.sc.level, ai)
                if sd > 0:
                    bonus += sd
                    event = f"{event}+{sev}" if event else sev

        if bonus:
            if self.staggered_until > frame:
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
        for delay, d, label in followups:
            self.pending.append({
                "frame": frame + int(delay), "cid": cid, "mult": 0.0,
                "poise": 0.0, "element": None, "slot": "dot",
                "abnormal": None, "applies_element": False, "no_rx": True,
                "flat": round(d), "label": label,
            })

        poise = item["poise"]
        if poise > 0:
            self.enemy_poise += poise
            if self.enemy_poise >= self.sc.enemy_poise:
                self.enemy_poise = 0.0
                self.staggered_until = frame + int(10 * config.FPS)
                self._open_windows_for("on_break", frame)
                self.log.append({"frame": frame, "kind": "stagger", "op": None,
                                 "label": "敌人失衡", "damage": 0.0})


    # ------------------------------------------------------------ 天赋/潜能

    _ATTR_MAIN = {39: "str", 40: "agi", 41: "wisd", 42: "will"}
    # 属性 id → 生效范围（来源：AttributeMetaTable 的 iconName 字段）
    #   17 普攻效率 / 32 战技效率 / 33 连携技效率 / 28 终结技效率
    #   50~55 元素伤害加成（物理/灼热/电磁/寒冷/自然/超域）
    #   61 对失衡单位伤害提升；29 治疗输出（不参与伤害）；64~69 无图标，未定性
    _ATTR_SLOT = {17: ("normal_attack", "power_attack", "dash_attack"),
                  32: ("battle_skill",), 33: ("combo_skill",), 28: ("ultimate",)}
    _ATTR_ELEMENT = {50: "物理", 51: "灼热", 52: "电磁",
                     53: "寒冷", 54: "自然", 55: "超域"}

    def _new_mod(self) -> dict:
        return {"attrs": {}, "atk_flat": 0.0, "atk_pct": 0.0,
                "dmg_bonus": 0.0,          # 通用伤害加成（套装黑板等）
                "dmg_slot": {},           # 按技能槽位的效率加成
                "dmg_element": {},        # 按元素（伤害类型）的加成
                "dmg_stagger": 0.0,       # 对失衡单位的伤害加成
                "heal_bonus": 0.0,        # 治疗量加成（不参与伤害）
                "ai": 0.0, "bb": [], "applied": [], "ignored_attrs": []}

    # 新属性 id 的兜底归类：AttributeMetaTable.iconName 的命名是稳定的，
    # 新版本加属性时通常还能对上（对不上的会进 ignored_attrs，不会被当成通用增伤）。
    _ICON_RULES = (
        ("physical_damage_increase", ("element", "物理")),
        ("fire_damage_increase", ("element", "灼热")),
        ("pulse_damage_increase", ("element", "电磁")),
        ("cryst_damage_increase", ("element", "寒冷")),
        ("natural_damage_increase", ("element", "自然")),
        ("ether_damage_increase", ("element", "超域")),
        ("normal_attack_efficiency", ("slots", ("normal_attack", "power_attack", "dash_attack"))),
        ("normal_atk_efficiency", ("slots", ("normal_attack", "power_attack", "dash_attack"))),
        ("normal_skill_efficiency", ("slots", ("battle_skill",))),
        ("combo_skill_efficiency", ("slots", ("combo_skill",))),
        ("ultimate_skill_efficiency", ("slots", ("ultimate",))),
        ("heal_output_increase", ("heal", None)),
        ("damage_to_broken_unit", ("stagger", None)),
    )

    def _attr_category(self, a: int):
        """返回 (类别, 参数)。已知 id 优先；未知 id 用 iconName 归类。"""
        if a in self._ATTR_MAIN:
            return "main", self._ATTR_MAIN[a]
        if a == 87:
            return "ai", None
        if a == 2:
            return "atk_flat", None
        if a in self._ATTR_SLOT:
            return "slots", self._ATTR_SLOT[a]
        if a in self._ATTR_ELEMENT:
            return "element", self._ATTR_ELEMENT[a]
        if a == 61:
            return "stagger", None
        if a == 29:
            return "heal", None
        if a in (1, 3, 4, 5, 6, 7, 44, 48, 60):
            return "skip", None
        icon = str(((self.attr_meta or {}).get(str(a)) or {}).get("icon") or "").lower()
        for pat, cat in self._ICON_RULES:
            if pat in icon:
                return cat
        return None

    def _add_mod_attr(self, m: dict, a, v: float, tag: str) -> None:
        """按属性 id 把数值放进对应的乘区（不再一律塞进通用伤害加成）。"""
        try:
            a = int(a)
        except (TypeError, ValueError):
            return
        if not v:
            return
        cat = self._attr_category(a)
        if cat is None:
            # 未定性属性：宁可不用，也不当成通用增伤
            m["ignored_attrs"].append(a)
            return
        kind, arg = cat
        if kind == "main":
            m["attrs"][arg] = m["attrs"].get(arg, 0.0) + v
            m["applied"].append(f"{tag}:{arg}+{v:g}")
        elif kind == "ai":
            m["ai"] += v
            m["applied"].append(f"{tag}:AI+{v:g}")
        elif kind == "atk_flat":
            m["atk_flat"] += v
            m["applied"].append(f"{tag}:ATK+{v:g}")
        elif kind == "slots":
            for slot in arg:
                m["dmg_slot"][slot] = m["dmg_slot"].get(slot, 0.0) + v
            m["applied"].append(f"{tag}:{arg[0]}+{v:.1%}")
        elif kind == "element":
            m["dmg_element"][arg] = m["dmg_element"].get(arg, 0.0) + v
            m["applied"].append(f"{tag}:{arg}+{v:.1%}")
        elif kind == "stagger":
            m["dmg_stagger"] += v
            m["applied"].append(f"{tag}:失衡+{v:.1%}")
        elif kind == "heal":
            m["heal_bonus"] += v
        elif kind == "skip":
            return


    _EQUIP_CACHE: dict | None = None

    @classmethod
    def _equip_table(cls) -> dict:
        if Battle._EQUIP_CACHE is None:
            p = config.NORM / "equip.json"
            try:
                Battle._EQUIP_CACHE = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                Battle._EQUIP_CACHE = {"items": {}, "suits": {}}
        return Battle._EQUIP_CACHE

    def _equip_acc(self, ids: list[str]) -> dict[str, float]:
        """4 件装备的属性累加（按精锻等级），并叠加同套 >= need 件的套装被动。"""
        table = (self._equip_table().get("items") or {})
        lv = max(0, min(int(self.sc.equip_level), 3))
        acc: dict[str, float] = {}
        for iid in ids:
            row = table.get(iid) or {}
            for attr, vals in (row.get("attrs") or {}).items():
                if not vals:
                    continue
                v = float(vals[min(lv, len(vals) - 1)] or 0.0)
                if v:
                    acc[attr] = acc.get(attr, 0.0) + v
        # 套装：同套 >= need 件时施加被动黑板
        suits = (self._equip_table().get("suits") or {})
        counts: dict[str, int] = {}
        for iid in ids:
            for sid, srow in suits.items():
                if iid in (srow.get("pieces") or []):
                    counts[sid] = counts.get(sid, 0) + 1
        for sid, n in counts.items():
            srow = suits.get(sid) or {}
            if n < int(srow.get("need", 3)):
                continue
            for k, v in (srow.get("passive_bb") or {}).items():
                if k in ("str", "agi", "wisd", "will"):
                    acc["__attr_" + k] = acc.get("__attr_" + k, 0.0) + float(v)
                elif k in ("atk",):
                    acc["__atk_pct__"] = acc.get("__atk_pct__", 0.0) + float(v)
                elif k in ("atk_up", "dmg_up", "dmg"):
                    acc["__dmg__"] = acc.get("__dmg__", 0.0) + float(v)
                elif k.endswith("_dmg_up"):
                    acc["__dmg__"] = acc.get("__dmg__", 0.0) + float(v)
                elif k in ("agi_up", "str_up", "wisd_up", "will_up"):
                    acc["__attr_" + k[:-3]] = acc.get("__attr_" + k[:-3], 0.0) + float(v)
        return acc

    def _apply_equipment(self) -> None:
        """把装备属性加到干员身上。

        * `equip_by_op`（每角色 4 件）优先，推荐给装是按人分工的；
        * 其次 `equip_ids`（全队同一套，旧行为）；
        * 两者都为空 = 不穿装备，行为与之前完全一致。
        """
        team_ids = list(self.sc.equip_ids or [])
        per_op = {k: list(v) for k, v in (self.sc.equip_by_op or {}).items() if v}
        if not team_ids and not per_op:
            return
        cache: dict[tuple, dict] = {}
        for cid in self.ids:
            ids = per_op.get(cid) or team_ids
            if not ids:
                continue
            key = tuple(ids)
            if key not in cache:
                cache[key] = self._equip_acc(ids)
            acc = cache[key]
            if not acc:
                continue
            m = self.mod[cid]
            for attr, v in acc.items():
                if attr == "__atk_pct__":
                    m["atk_pct"] += v
                    m["applied"].append(f"SET:atk+{v:.1%}")
                    continue
                if attr == "__dmg__":
                    m["dmg_bonus"] += v
                    m["applied"].append(f"SET:dmg+{v:.1%}")
                    continue
                if attr.startswith("__attr_"):
                    k2 = attr[7:]
                    m["attrs"][k2] = m["attrs"].get(k2, 0.0) + v
                    m["applied"].append(f"SET:{k2}+{v:g}")
                    continue
                if attr.startswith("__"):
                    continue
                self._add_mod_attr(m, attr, v, "EQ")

    def _build_mods(self) -> dict:
        """把天赋/潜能里**无条件**的数值修正整理成 {cid: {...}}。

        只取 `activeCondition` 为空的条目（无条件的常驻加成），
        条件型（叠层/血量/状态）暂不施加，避免高估。
        """
        out: dict[str, dict] = {}
        for cid in self.ids:
            op = self.ops[cid]
            mods = op.mods or {}
            m = self._new_mod()

            def take(entries, tag):
                for e in entries or []:
                    if e.get("conditional"):
                        continue
                    if e.get("kind") == "bb":
                        k = str(e.get("key") or "")
                        v = float(e.get("value") or 1.0)
                        if k and v and v != 1.0:
                            m["bb"].append({"key": k, "value": v,
                                            "skill_id": e.get("skill_id") or ""})
                            m["applied"].append(f"{tag}:{k}×{v:g}")
                        continue
                    if e.get("kind") != "attr":
                        continue
                    self._add_mod_attr(m, e.get("attr"),
                                       float(e.get("value") or 0), tag)

            for tal, levels in (mods.get("talents") or {}).items():
                if not levels:
                    continue
                lv = str(max(int(x) for x in levels))
                take(levels.get(lv), f"T{tal}")
            for k in range(1, int(self.sc.potential_level) + 1):
                take((mods.get("potentials") or {}).get(str(k)), f"P{k}")

            # 武器被动：wpn_attr_*（属性）/ wpn_sp_attr_*（atk% 等）
            wbb = self._weapon_row(cid).get("skill_bb") or []
            if wbb:
                for k, v in (wbb[0] or {}).items():
                    if k in ("str", "agi", "wisd", "will"):
                        m["attrs"][k] = m["attrs"].get(k, 0.0) + float(v)
                        m["applied"].append(f"W:{k}+{v:g}")
                    elif k == "atk":
                        m["atk_pct"] += float(v)
                        m["applied"].append(f"W:atk+{float(v):.1%}")
                    elif k == "mainattr":
                        m["attrs"]["__mainattr__"] = (
                            m["attrs"].get("__mainattr__", 0.0) + float(v))
                        m["applied"].append(f"W:mainattr+{float(v):g}")

            out[cid] = m
        return out



    def _scaled_mult(self, cid: str, sk) -> float:
        """按"逐命中 key"缩放倍率：天赋只能乘在该 key 上，不能乘在总和上。"""
        lv = sk.level(self.sc.skill_level)
        if not lv:
            return sk.total_multiplier(self.sc.skill_level)
        entries = (self.mod.get(cid) or {}).get("bb") or []
        total = 0.0
        for h in sk.timing.hits:
            if h.damage_type not in (0, None):
                continue
            base = sk._resolve_mult(lv.multipliers, h.key)
            scale = 1.0
            best: dict[tuple[str, str], float] = {}
            for e in entries:
                sid = e.get("skill_id") or ""
                if sid and sid != sk.skill_id:
                    continue
                if e["key"] != h.key:
                    continue
                k = (e["key"], sid)
                best[k] = max(best.get(k, 1.0), float(e["value"]))
            for v in best.values():
                scale *= v
            total += base * scale
        return total if total else sk.total_multiplier(self.sc.skill_level)

    def _bb_scale(self, cid: str, sk) -> float:
        """天赋/潜能对技能黑板倍率的修正（如 战技倍率 ×1.1）。"""
        entries = (self.mod.get(cid) or {}).get("bb") or []
        if not entries:
            return 1.0
        keys = {h.key for h in sk.timing.hits if h.key}
        # 同一 (key, skillId) 的多条记录是重复登记，取最大值而不是连乘
        best: dict[tuple[str, str], float] = {}
        for e in entries:
            sid = e.get("skill_id") or ""
            if sid and sid != sk.skill_id:
                continue          # 只作用于指定技能
            if e["key"] not in keys:
                continue
            k = (e["key"], sid)
            best[k] = max(best.get(k, 1.0), float(e["value"]))
        factor = 1.0
        for v in best.values():
            factor *= v
        return factor

    def _ai(self, cid: str) -> float:
        """源石技艺强度（attrType 87）。"""
        row = self.ops[cid].stat_at(self.sc.level)
        return (float(row.get("arts_intensity") or 0.0)
                + float((self.mod.get(cid) or {}).get("ai", 0.0)))

    def _hit_damage(self, cid: str, mult: float, element: str | None,
                    frame: int, *, apply_status: bool = True,
                    slot: str | None = None) -> float:
        if mult <= 0:
            return 0.0
        m = self.mod.get(cid) or {}
        atk = self._atk(cid)
        res = self.sc.enemy_res
        if apply_status:
            res = max(0.0, res - self.rx.res_shred(frame) / 100.0)
        d = atk * mult
        d *= 1.0 + float(m.get("dmg_bonus", 0.0))
        # 按技能槽位 / 元素 / 失衡的专项加成（AttributeMetaTable 的口径）
        if slot:
            d *= 1.0 + float((m.get("dmg_slot") or {}).get(slot, 0.0))
        if element:
            d *= 1.0 + float((m.get("dmg_element") or {}).get(element, 0.0))
        d *= 100.0 / (max(0.0, self.sc.enemy_def) + 100.0)
        d *= max(0.0, 1.0 - res)
        if self.staggered_until > frame:
            d *= config.guess("STAGGER_DMG_MULT") * (
                1.0 + float(m.get("dmg_stagger", 0.0)))
        if apply_status and element in rxmod.ARTS_ELEMENTS:
            d *= self.rx.arts_dmg_taken(frame)
        return round(d)

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
        mult = self._scaled_mult(cid, sk)
        element = sk.element or op.element
        hits = [h for h in (sk.timing.hit_frames or [commit]) if 0 < h <= commit]
        if not hits:
            hits = [max(1, commit // 2)]
        n = max(1, len(hits))
        share = mult / n
        poise_share = float(lv.multipliers.get("poise") or 0.0) / n
        for hf in hits:
            frame = start + int(hf)
            self.pending.append({
                "frame": frame, "cid": cid, "mult": share,
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
            # 重击回技力按该干员 attack5 的黑板 atb（wiki：15~30，按人不同）
            gain = float(lv.multipliers.get("atb") or 0.0)
            if gain <= 0:
                gain = config.guess("SP_GAIN_HEAVY")
            self.sp = min(config.guess("SP_MAX"), self.sp + gain)
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
        b.samples = [dict(x) for x in self.samples]
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
            "buff/debuff 体系（BuffData 的 attributeModifier 叠加）",
            "武器条件型技能（sk_wpn_*，如“攻击力+12% 持续 20 秒”）",
            "暴击随机性（用期望值）",
            "敌人受击硬直与真实 AI 节奏（固定间隔出手）",
            "连携技的额外条件（如层数、消耗）",
            "多段技能的分支判定（if/else 无法区分）",
        ]
        warnings.append("技力恢复速度、切人开销等为 estimate，可在 config.py 校准")
        # 水位采样：补上结束点，保证 UI 的曲线覆盖整个窗口
        samples = list(self.samples)
        if not samples or samples[-1]["f"] != self.t:
            samples.append({"f": self.t, "sp": round(self.sp, 1),
                            "e": {c: round(v, 1) for c, v in self.energy.items()}})
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
                   "sp_max": float(config.guess("SP_MAX")),
                   "energy_left": {c: round(v, 1) for c, v in self.energy.items()},
                   "energy_cap": {c: round(self._energy_cap(c), 1) for c in self.ids},
                   "weapons": {c: (self.sc.weapon_by_op or {}).get(c)
                               or (self.ops[c].weapon or {}).get("id")
                               for c in self.ids},
                   "reactions": self.rx.to_dict(),
                   "samples": samples[-1500:]},
        )
