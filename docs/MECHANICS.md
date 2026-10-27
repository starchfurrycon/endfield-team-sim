# 战斗机制对照表（v3.9.0）

这份文件把「游戏规则 → 引擎实现 → 状态」列出来，避免再出现"看起来合理"的误解。
来源：`docs/01-调研.md`、`docs/reference/docs__design.md`（参考实现 Endaxis/efsim）、
wiki 技能帧数据。

## 1. 已建模（有测试）

| 规则 | 实现 | 测试 |
|---|---|---|
| 只有主控能普攻 / 重击 | `legal_actions` 只给 `main` 发 `attack`；非主控发 `switch` | `TestMainControl.test_attacks_only_for_main` |
| 主控可换人 | 显式 `Action("switch")`，付切人开销；非主控放战技/终结技会先换人 | `test_switch_changes_main` |
| 换人后由新主控打重击 | 重击只在主控的普攻链末段 | `test_new_main_can_heavy` |
| 重击回技力 | 只对主控的重击加 SP（`SP_GAIN_HEAVY` / `atb`） | `test_heavy_gives_sp` |
| 非主控不自动攻击 | 删除了旧版"主控外自动普攻" | `test_no_offfield_auto_attack` |
| 连携是插入动作，不改变主控 | `apply(combo)` 不换人、只开窗口/冷却 | `test_guide` 的步骤 |
| 战技无冷却、消耗 1 段技力 | SP 池 300、每段 100（`SP_REGEN_PER_SEC` 自然恢复） | `test_core` |
| 终结技需能量（70–300） | 每人能量上限来自数据；战技给全队 +6.5，连携给自身 | `test_mechanics` |
| 帧级承诺（exclusiveFrame） | `commit_frames` 限制动作锁 | `test_core` |
| 失衡（poise）与 10s 窗口 | 命中累积 poise，满值失衡并开 `on_break` 窗口 | `test_mechanics` |
| 元素附着/爆发/异常、物理破防 | `reactions.py` | `test_mechanics` |
| 主控受击开连携窗口 | `on_hit_taken` | `test_mechanics` |

## 2. 近似 / 待确认（写清楚，不假装）

| 规则 | 当前处理 | 风险 |
|---|---|---|
| 切人开销帧数 | `SWITCH_FRAMES` 估计值，可配置 | 换人轴的绝对时间可能偏 |
| 战技/终结技是否必须主控 | 按"长按战技释放"处理：非主控先换人 | 若实际允许队友直接放，会低估技能频率 |
| 连携插入是否占用主控时间 | 不占，只是插入 | 连携多的队伍可能被高估 |
| 连携窗口 5s | 由 `WINDOW_FRAMES` 估计 | 窗口长度影响连携数量 |
| 敌人出手节奏 | 固定间隔（8s 默认） | 真实 AI 会变 |
| 暴击 | 期望值 | 与实测有偏差 |

## 3. 未建模（结果里会声明）

- 处决攻击（敌人失衡时的大额伤害 + 回技力）；
- 闪避 / 完美闪避回技力与无敌帧；
- buff/debuff 体系（BuffData 叠加）、武器条件型技能；
- 多段技能的分支判定（if/else）；
- 敌人受击硬直、真实 AI 与位移。

## 4. 下一步（按影响排序）

1. 把"战技/终结技是否必须主控"做成可配置开关（`Scenario` 字段 + UI）；
2. 处决攻击：失衡窗口内可用 `Action("execute")`，伤害与回 SP 按数据；
3. 连携插入是否占主控时间：用日志/实测标定；
4. 切人开销：从动画数据取，不再用估计值。

> 如果你知道上面哪条是错的，直接指出来，我按这条改引擎 + 补测试，
> 不再用"看起来合理"的方式补。
