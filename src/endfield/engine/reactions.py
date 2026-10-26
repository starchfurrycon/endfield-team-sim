"""元素 / 物理异常伤害子系统（按 Talos Wiki + 客户端表还原）。

规则来源：
  * Arts Infliction / Arts Burst / Arts Reaction / Combustion / Arts Intensity 页面
  * docs/01-调研.md §2.4–2.5
  * 客户端 `buff_dung_atb_*` 等规则 buff

核心规则（与 wiki 一致）：
  * 附着持续 20 秒，最多 4 层；同元素再命中 → **爆发 160% ATK**
  * 异元素命中 → **异常**：已有附着（stacking）决定**状态等级 I~IV**，
    新元素决定**异常类型**，并造成一次法术伤害
  * 法术伤害倍率：`base × (195+Lv)/196 × (1+AI/100)`
    - 燃烧：初始 160/240/320/400% ATK，并按 24/36/48/60% ATK/秒 持续 10 秒
    - 导电：初始同倍率，并给敌人 +12/16/20/24% 法术受伤
    - 冻结：初始同倍率并禁锢，被物理异常击碎时触发碎冰
    - 腐蚀：初始同倍率，并按 3.6/4.8/6/7.2 点起、每秒 +0.84/1.12/1.4/1.68、
      上限 12/16/20/24 降低抗性
  * 物理：击飞/倒地叠**破防**（≤4）；猛击 150%+150%/层、碎甲 50%+50%/层；
    击飞/倒地触发 120%
"""

from __future__ import annotations

from dataclasses import dataclass, field

FPS = 30

ARTS_ELEMENTS = ("灼热", "电磁", "寒冷", "自然")
LIFT_KINDS = ("击飞", "倒地")
CONSUME_KINDS = ("猛击", "碎甲")

REACTION_OF = {"灼热": "燃烧", "电磁": "导电", "寒冷": "冻结", "自然": "腐蚀"}

BURST_MULT = 1.6
REACTION_INITIAL_MULT = 0.8
LIFT_MULT = 1.2
CRUSH_MULT = 1.5
BREACH_MULT = 0.5
SHATTER_MULT = 1.2

MAX_VULN = 4
MAX_ARTS_STACKS = 4
INFLICTION_FRAMES = 20 * FPS

COMBUSTION_DOT_PER_S = 0.12
COMBUSTION_FRAMES = 10 * FPS
COMBUSTION_TICK = FPS

ELECTRIFICATION_FRAMES = 12 * FPS
SOLIDIFY_FRAMES = 6 * FPS
CORROSION_FRAMES = 15 * FPS


def level_coef(operator_level: int) -> float:
    return (195 + max(1, operator_level)) / 196.0


def phys_coef(operator_level: int) -> float:
    return (195 + max(1, operator_level)) / 392.0


def ai_mult(arts_intensity: float) -> float:
    return 1.0 + max(0.0, arts_intensity) / 100.0


@dataclass
class ReactionState:
    arts_attached: str | None = None
    arts_stacks: int = 0
    arts_until: int = -1
    vuln: int = 0
    vuln_until: int = -1
    statuses: dict[str, dict] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    # ---------------------------------------------------------- 查询

    def _alive(self, name: str, frame: int) -> dict | None:
        st = self.statuses.get(name)
        if not st or st.get("until", -1) <= frame:
            return None
        return st

    def arts_dmg_taken(self, frame: int) -> float:
        st = self._alive("导电", frame)
        if not st:
            return 1.0
        return 1.0 + (8 + 4 * st["level"]) / 100.0

    def res_shred(self, frame: int) -> float:
        st = self._alive("腐蚀", frame)
        if not st:
            return 0.0
        lv = st["level"]
        elapsed = max(0.0, (frame - st["start"]) / FPS)
        return min(12.0 * lv, 3.6 * lv + 0.84 * lv * elapsed)

    def is_frozen(self, frame: int) -> bool:
        return self._alive("冻结", frame) is not None

    def vulnerable(self, frame: int) -> int:
        return self.vuln if self.vuln_until > frame else 0

    # ---------------------------------------------------------- 法术

    def apply_element(self, element: str, atk: float, frame: int,
                      level: int, ai: float = 0.0
                      ) -> tuple[float, str | None, list[tuple[int, float, str]]]:
        if element not in ARTS_ELEMENTS:
            return 0.0, None, []
        if self.arts_until <= frame:
            self.arts_attached = None
            self.arts_stacks = 0

        coef = level_coef(level) * ai_mult(ai)

        if self.arts_attached is None:
            self.arts_attached = element
            self.arts_stacks = 1
            self.arts_until = frame + INFLICTION_FRAMES
            self._bump("附着", 0.0)
            return 0.0, "附着", []

        if self.arts_attached == element:
            self.arts_stacks = min(self.arts_stacks + 1, MAX_ARTS_STACKS)
            self.arts_until = frame + INFLICTION_FRAMES
            dmg = BURST_MULT * coef * atk
            self._bump("爆发", dmg)
            return dmg, "爆发", []

        lv = max(1, min(self.arts_stacks, MAX_ARTS_STACKS))
        kind = REACTION_OF[element]
        dmg = REACTION_INITIAL_MULT * (1 + lv) * coef * atk

        followups: list[tuple[int, float, str]] = []
        if kind == "燃烧":
            tick = COMBUSTION_DOT_PER_S * (1 + lv) * coef * atk
            for i in range(1, COMBUSTION_FRAMES // COMBUSTION_TICK + 1):
                followups.append((i * COMBUSTION_TICK, tick, "燃烧"))
            self.statuses["燃烧"] = {"level": lv, "start": frame,
                                     "until": frame + COMBUSTION_FRAMES}
        elif kind == "导电":
            self.statuses["导电"] = {"level": lv, "start": frame,
                                     "until": frame + ELECTRIFICATION_FRAMES}
        elif kind == "冻结":
            self.statuses["冻结"] = {"level": lv, "start": frame,
                                     "until": frame + SOLIDIFY_FRAMES}
        else:
            self.statuses["腐蚀"] = {"level": lv, "start": frame,
                                     "until": frame + CORROSION_FRAMES}

        self._bump(kind, dmg)
        self.arts_attached = element
        self.arts_stacks = 1
        self.arts_until = frame + INFLICTION_FRAMES
        return dmg, kind, followups

    def consume_frozen(self, frame: int, atk: float, level: int,
                       ai: float = 0.0) -> tuple[float, str | None]:
        st = self._alive("冻结", frame)
        if not st:
            return 0.0, None
        del self.statuses["冻结"]
        dmg = SHATTER_MULT * (1 + st["level"]) * level_coef(level) * ai_mult(ai) * atk
        self._bump("碎冰", dmg)
        return dmg, "碎冰"

    # ---------------------------------------------------------- 物理

    def apply_physical(self, kind: str | None, atk: float, frame: int,
                       level: int, ai: float = 0.0) -> tuple[float, str | None]:
        if not kind:
            return 0.0, None
        coef = phys_coef(level) * ai_mult(ai)
        if kind in LIFT_KINDS:
            if self.vulnerable(frame) <= 0:
                self.vuln = 1
                self.vuln_until = frame + INFLICTION_FRAMES
                self._bump("破防", 0.0)
                return 0.0, "破防"
            self.vuln = min(self.vulnerable(frame) + 1, MAX_VULN)
            self.vuln_until = frame + INFLICTION_FRAMES
            dmg = LIFT_MULT * coef * atk
            self._bump(kind, dmg)
            return dmg, kind
        if kind in CONSUME_KINDS:
            n = self.vulnerable(frame)
            if n <= 0:
                return 0.0, None
            self.vuln = 0
            base = CRUSH_MULT if kind == "猛击" else BREACH_MULT
            dmg = base * (1 + n) * coef * atk
            self._bump(kind, dmg)
            return dmg, kind
        return 0.0, None

    # ---------------------------------------------------------- 工具

    def _bump(self, name: str, dmg: float) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1
        if dmg:
            self.totals[name] = self.totals.get(name, 0.0) + dmg

    def to_dict(self) -> dict:
        return {
            "arts_attached": self.arts_attached,
            "arts_stacks": self.arts_stacks,
            "vuln": self.vuln,
            "statuses": {k: v.get("level") for k, v in self.statuses.items()},
            "totals": {k: round(v, 1) for k, v in self.totals.items()},
            "counts": dict(self.counts),
        }

    def clone(self) -> "ReactionState":
        return ReactionState(
            self.arts_attached, self.arts_stacks, self.arts_until,
            self.vuln, self.vuln_until,
            {k: dict(v) for k, v in self.statuses.items()},
            dict(self.totals), dict(self.counts))
