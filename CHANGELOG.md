# Changelog

## v0.1.0-beta (2026-10-25)

首个可分发测试版。功能完整，但**数值精度是"排序器"级别**，请先读「已知限制」。

### 能做什么

- **约束配队推荐**：给定「必须包含 / 禁止包含 / 可选池 / 队伍人数 / 主控」，
  输出 Top-N 配队，附评分与理由。
- **冷启动轴（终结技能量 0）与热启动轴（终结技能量满）**：
  每队两条，逐动作列出时间、结束时刻、命中帧。
- **帧级时间轴**：30 fps，动作承诺帧与命中帧直接来自客户端 `Json/SkillData`。
- **数据自更新**：`sync` 会解析数据仓 HEAD 与 Wiki 版本，写 `manifest.json`，
  并生成漂移报告。
- **桌面 UI**：Electron 壳 + 本地 stdlib 服务；干员三态选择、甘特图式轴、伤害贡献。

### 已核实的数据口径（见 `data/reports/mechanics.md`）

| 机制 | 值 | 出处 |
|---|---|---|
| 重击回技力 | 100 点（1 段） | `buff_dung_atb_add_lastnormalattack` atb=1.0 |
| 极限闪避回技力 | 100 点 | `buff_dung_atb_add_perfectdodge` |
| 击杀回技力 | 50 点 | `buff_dung_atb_add_kill` |
| 战技给全队终结技能量 | 6.5 | `buff_common_obtain_ultimate_sp` usp_everyone |
| 连携技回能 | 技能黑板 `usp` | SkillPatchTable |
| 连携技不消耗技力 / 战技无冷却 | — | 游戏内教学页 `wiki_tut_bat_combo_2` / `wiki_tut_bat_normal_skill_1` |
| 主控外干员自动普攻 | — | 教学页 `wiki_tut_bat_team_1` |

### 本版修掉的问题

- 萤石终结技、洛茜战技因黑板 key 命名不同（`atk_scale_total` / `atk_scale_once`）
  导致倍率恒为 0（等于技能不存在）→ 增加命名兜底链。
- 热启动轴可能比冷启动更差（搜索启发式导致）→ 新增 `search_pair`，
  用冷启动的完整动作序列在热启动条件下重放取下界。
- `RotationResult.actions` 只含搜索前缀 → 改为从 timeline 还原完整序列。
- 主控以外干员完全不出伤害 → 实现自动普攻排程（不占全局动作锁、不回技力）。

### 已知限制

- **绝对伤害偏低**：未建模 buff/debuff、元素异常与物理异常伤害、武器与装备加成。
  请只用于相对比较。
- **自然技力恢复速率**仍是估计值（8 点/秒）。客户端表里
  `buff_dung_atb_recoverspeed` 这个规则 buff 存在但黑板为空，速率未随表导出。
- 热启动收益可能仍被低估：终结技的时间停止、终结技专属增伤未建模。
- 6 个技能倍率为 0（赛希/昼雪/秋栗/安塔尔/庄方宜等，多为护盾/治疗/召唤类）。
- 敌人行为是"固定间隔出手"的场景参数，不是真实 AI。

### 测试

`python -m unittest discover -s tests` → 29 项，全绿。
