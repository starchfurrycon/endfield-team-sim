"""语义层：把技能中文描述变成可执行的结构化标签。

为什么需要：帧数据和倍率解决了"多少"，但"什么条件下能放"、
"放了会产生什么状态"只在自然语言里。引擎必须有这些标签才能判断连携触发、
是否依赖受击、是否叠异常。

原则（计划 §1.2）：**LLM 不产出数值**。本层的输出只有枚举/布尔/时长，
倍率永远来自数据表。
"""

from .rules import extract_skill, extract_operator  # noqa: F401
from .schema import validate_semantics, SEMANTICS_SCHEMA  # noqa: F401
