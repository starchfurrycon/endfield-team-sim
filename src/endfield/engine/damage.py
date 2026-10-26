"""伤害公式的可复用片段。

分工：
  * 这里只保留**被引擎直接调用**的纯函数（目前是 ATK 公式）；
  * 完整的一次伤害结算在 `engine/battle.py:_hit_damage`，
    因为它要读实时状态（抗性、易伤、失衡、源石技艺强度），
    在单测里复现那份逻辑只会得到"两份公式"，所以删掉了旧的通用 compute()。
"""

from __future__ import annotations

from .. import config


def effective_atk(base_atk: float, main_attr: float, sub_attr: float,
                  weapon_atk: float = 0.0) -> float:
    """ATK = (base + weapon) × (1 + 0.005×主属性 + 0.002×副属性)"""
    return ((base_atk + weapon_atk)
            * (1.0 + config.guess("ATK_RATE_MAIN") * int(main_attr)
               + config.guess("ATK_RATE_SUB") * int(sub_attr)))
