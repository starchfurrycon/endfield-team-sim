"""元素 / 物理异常伤害子系统。

规则来源（见 docs/01-调研.md §2.5 与 §2.4）：
  * 法术：附着 → 爆发（同元素再次命中，160%）→ 异常（异元素命中，80% ×(1+附着层数)）
  * 物理：击飞/倒地 → 叠「破防」（最多 4 层）；猛击/碎甲 → 消耗层数打爆发
    （猛击 150% +150%/层，碎甲 50% +50%/层，击飞/倒地触发时 120%）
  * 等级系数：法术与冻结 1+(Lv−1)/196，物理状态 1+(Lv−1)/392

置信度：倍率来自社区共识（Talos / 666bj），**未逐条实测**；
因此这里的收益会计入 Result.reaction_totals 并单独标注，
方便将来用日志校准。燃烧 DoT、导电/腐蚀 的减抗与增伤暂未建模。
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARTS_ELEMENTS = ("灼热", "电磁", "寒冷", "自然")
PHYS_ELEMENTS = ("物理",)
LIFT_KINDS = ("击飞", "倒地")
CONSUME_KINDS = ("猛击", "碎甲")

BURST_MULT = 1.6          # 法术爆发
REACTION_MULT = 0.8       # 法术异常（每层附着 +80%）
LIFT_MULT = 1.2           # 击飞/倒地触发伤害
CRUSH_MULT = 1.5          # 猛击：每层破防 +150%
BREACH_MULT = 0.5         # 碎甲：每层破防 +50%
MAX_VULN = 4
MAX_ARTS_STACKS = 4   # 附着层数上限（社区口径 4 层）


def arts_coef(level: int) -> float:
    return 1.0 + (max(1, level) - 1) / 196.0


def phys_coef(level: int) -> float:
    return 1.0 + (max(1, level) - 1) / 392.0


@dataclass
class ReactionState:
    arts_attached: str | None = None
    arts_stacks: int = 0
    vuln: int = 0                 # 破防层数
    totals: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------- 法术

    def apply_element(self, element: str, atk: float, frame: int,
                      level: int) -> tuple[float, str | None]:
        """返回 (额外伤害, 事件名)。"""
        if element not in ARTS_ELEMENTS:
            return 0.0, None
        coef = arts_coef(level)
        if self.arts_attached is None:
            self.arts_attached = element
            self.arts_stacks = 1
            self._bump("附着", 0.0)
            return 0.0, "附着"
        if self.arts_attached == element:
            self.arts_stacks = min(self.arts_stacks + 1, MAX_ARTS_STACKS)
            dmg = BURST_MULT * coef * atk
            self._bump("爆发", dmg)
            return dmg, "爆发"
        # 异元素 → 异常
        dmg = REACTION_MULT * (1 + self.arts_stacks) * coef * atk
        self._bump("异常", dmg)
        self.arts_attached = element
        self.arts_stacks = 1
        return dmg, "异常"

    # ---------------------------------------------------------- 物理

    def apply_physical(self, kind: str | None, atk: float, frame: int,
                       level: int) -> tuple[float, str | None]:
        if not kind:
            return 0.0, None
        coef = phys_coef(level)
        if kind in LIFT_KINDS:
            if self.vuln <= 0:
                self.vuln = 1
                self._bump("破防", 0.0)
                return 0.0, "破防"
            self.vuln = min(self.vuln + 1, MAX_VULN)
            dmg = LIFT_MULT * coef * atk
            self._bump(kind, dmg)
            return dmg, kind
        if kind in CONSUME_KINDS:
            n = self.vuln
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
            "totals": {k: round(v, 1) for k, v in self.totals.items()},
            "counts": dict(self.counts),
        }

    def clone(self) -> "ReactionState":
        return ReactionState(self.arts_attached, self.arts_stacks, self.vuln,
                             dict(self.totals), dict(self.counts), list(self.log))
