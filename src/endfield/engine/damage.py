"""伤害计算。

只实现"能接地"的乘区；其余乘区显式置 1 并写进 Result.unmodeled。
公式来源见 docs/01-调研.md §2.5。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import config


@dataclass
class DamageContext:
    """一次伤害结算的输入。"""
    atk: float
    multiplier: float
    element: str | None = None
    skill_slot: str = "normal_attack"
    staggered: bool = False
    enemy_def: float = 100.0
    enemy_res: float = 0.0          # 0~0.9
    crit_rate: float = config.guess("CRIT_RATE_BASE")
    crit_dmg: float = config.guess("CRIT_DMG_BASE")
    # 未建模乘区（默认 1.0，会出现在 unmodeled 列表里）
    dmg_bonus_pct: float = 0.0
    extra_mults: dict[str, float] = field(default_factory=dict)


def effective_atk(base_atk: float, main_attr: float, sub_attr: float,
                  weapon_atk: float = 0.0) -> float:
    """ATK = (base + weapon) × (1 + 0.005×主属性取整 + 0.002×副属性取整)"""
    return ((base_atk + weapon_atk)
            * (1.0 + config.guess("ATK_RATE_MAIN") * int(main_attr)
               + config.guess("ATK_RATE_SUB") * int(sub_attr)))


def expected_crit(crit_rate: float, crit_dmg: float) -> float:
    return 1.0 + crit_rate * crit_dmg


def compute(ctx: DamageContext) -> tuple[float, dict[str, float]]:
    """返回 (最终伤害, 各乘区明细)。"""
    zones: dict[str, float] = {}
    zones["atk"] = ctx.atk
    zones["base_mult"] = ctx.multiplier
    zones["dmg_bonus"] = 1.0 + ctx.dmg_bonus_pct
    zones["crit"] = expected_crit(ctx.crit_rate, ctx.crit_dmg)
    zones["def"] = 100.0 / (max(0.0, ctx.enemy_def) + 100.0)
    zones["res"] = max(0.0, 1.0 - ctx.enemy_res)
    zones["stagger"] = (config.guess("STAGGER_DMG_MULT")
                        if ctx.staggered else 1.0)
    for k, v in ctx.extra_mults.items():
        zones[k] = v

    dmg = 1.0
    for v in zones.values():
        dmg *= v
    return round(dmg), zones
