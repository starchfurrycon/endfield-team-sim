# 终末地配队模拟（Endfield Team Sim）· v2.3.0

《明日方舟：终末地》的**自动配队 + 输出轴**工具。

给定「必须包含 / 禁止包含 / 可选池 / 战斗场景」，输出：

- **Top-N 配队**（含评分与理由）
- 每队的 **冷启动轴**（终结技能量 0）与 **热启动轴**（终结技能量满）
- 逐动作的**帧级时间轴**（30 fps）与命中时刻
- **资源水位**（技力 / 终结技能量随时间的变化曲线）
- 每个数字的**来源**，以及**未建模项清单**

> ⚠️ 定位：**排序器 + 可解释建议**，不是"伤害真值机"。
> 已建模：技能倍率、武器 ATK 与被动、装备属性与套装被动、天赋/潜能、
> 法术爆发/异常、物理破防与猛击/碎甲。
> 未建模：buff/debuff 体系、武器条件型技能、暴击随机性、真实敌人 AI。
> 绝对数值会偏低，请只用它做**相对比较**。
> 详见 [`CHANGELOG.md`](CHANGELOG.md) 的「已知限制」。

---

## 快速开始

### 方式 A：下载发行版（推荐给只想起用的）

从 Releases 下载 `endfield-team-sim-2.3.0-win64.zip`，解压后双击：

```
endfield-server.exe
```

首次启动会**在后台从社区数据仓构建数据集**（需要联网，约 1~3 分钟）——
窗口里会显示**构建进度条**（阶段、百分比、已用时间），完成后自动进入界面。

> 之后每次启动都会直接复用本地数据；想更新游戏版本时删掉 `data/` 重开即可。

### 方式 B：从源码运行

```bash
python run.py sync      # 拉取/刷新数据源（GitHub 拆包表 + BWIKI）
python run.py build     # 归一化 + 语义抽取 + 各类报告
python run.py check     # 数据完整度 + 常量来源

python run.py team --must 陈千语 --ban 管理员 --top 3 --duration 40
python run.py axis --team 陈千语,佩丽卡,昼雪,余烬 --mode both --duration 40
python run.py axis --team 陈千语 --equip output --weapon 1   # 带装备/武器预设
python run.py serve --port 8777        # 本地 UI
```

桌面窗口（Electron）：

```bat
cd desktop
npm install            :: 卡住就设 ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
start.cmd
```

Python 3.10+，**无第三方依赖**（仅标准库）。

---

## 界面

- 左侧点干员：**一次 = 必须包含，两次 = 禁止，三次 = 清除**；可按元素 / TAG 过滤
- 右侧出 Top-N 配队卡，每张卡切换**冷启动轴 / 热启动轴**
- 轴用**甘特图**：每格 = 动作承诺时长，**白竖线 = 命中帧**；
  下方附动作表、伤害贡献、合法性告警、未建模提示
- **资源水位轨**：技力（全队共享 300）与每人终结技能量随时间变化的曲线，
  悬停读数——能一眼看出"卡技力"发生在哪一段
- **对比视图**：同一队的两套配置并排（启动方式 / 敌人节奏 / 装备预设 / 精锻 / 武器顺位），
  给出总伤、DPS 与百分比差值，两侧各一条完整轴
- **装备/武器推荐**：每人 3 把武器（按等效 ATK）+ 2 套装备（输出向 / 功能向），
  带中文名与精锻等级；可直接把预设带进对比
- 首次启动显示**构建进度**（阶段 + 百分比 + 已用时间），失败可重试

---

## 本地 API（UI 用的就是这些）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 构建状态：`state / percent / message / elapsed_s / ready` |
| GET | `/api/meta` | 干员列表 + 常量来源 + 可选预设；构建中返回 503 |
| GET | `/api/axis` | 单队一条轴：`team / mode / duration / enemy / equip / weapon` |
| POST | `/api/team` | 约束配队 Top-N |
| POST | `/api/compare` | 两套配置并排对比（`a` / `b` 各一份配置） |
| POST | `/api/loadout` | 每人的武器 3 选 + 装备 2 套 |

无第三方依赖（stdlib `http.server`），只监听 `127.0.0.1`。

---

## 它怎么工作

```
GitHub 拆包表 ─┐                    ┌─ 倍率/消耗/冷却（SkillPatchTable）
               ├─ 数据层 ─→ 归一化 ─┼─ 帧数据（SkillData：承诺帧/命中帧）
BWIKI 语义   ──┘                    └─ 中文描述/天赋/潜能
                                          │
                        语义层：文本 → trigger/元素/异常/受击依赖
                                          │
                        引擎：30fps 时间轴 + 技力/能量/连携窗口/失衡
                                          │
                        搜索：beam + rollout → 冷/热两条轴；约束下配队排序
```

关键设计：

1. **时间统一为帧**（1 帧 = 1/30 秒）。动作 = 承诺 `exclusiveFrame` 帧，之后可取消。
   —— 这是"不同主控普攻连段速度不一样"能被精确处理的原因。
2. **LLM 不产出数值**，只做语义分类与文本润色；数值永远来自数据表。
3. **未知必须可见**：未建模乘区、估计常量、估计命中帧，全部出现在结果里。

---

## 数据来源

| 源 | 用途 | 说明 |
|---|---|---|
| [rmxlinux/EndfieldData](https://github.com/rmxlinux/EndfieldData) | 技能倍率、帧数据、干员属性、规则 buff | 按 commit 锚点拉取 |
| [BWIKI 终末地](https://wiki.biligame.com/zmd/) | 中文描述、TAG、倍率交叉校验 | MediaWiki + SMW |

**本仓库不包含任何游戏数据**：`data/` 已被 `.gitignore` 排除，
运行时从上述社区镜像抓取并本地缓存。数据版权归官方所有。

游戏内机制原文与数值出处见 `data/reports/mechanics.md`（`python run.py mechanics`
可重新生成），它从客户端教学页与规则 buff 反查得到，例如：

- 重击回技力 **100 点（1 段）** ← `buff_dung_atb_add_lastnormalattack` `atb=1.0`
- 战技给**全队** +6.5 终结技能量 ← `buff_common_obtain_ultimate_sp` `usp_everyone=6.5`
- 连携技**不消耗技力**、战技**无冷却**、**主控以外自动普攻** ← 游戏内教学页

---

## 能力边界

**能做**：约束下的队伍相对强弱、可执行动作序列、受击/重击/连携窗口等时序机制、
**换装备/换武器前后的收益对比**、数据更新后重跑即可（`sync → build`）。

**已补**：武器 ATK 与被动、装备属性与套装被动、天赋/潜能、
法术爆发/异常、物理破防与猛击/碎甲。

**做不到**：绝对 DPS 准确（缺 buff/debuff 与武器条件型技能）、真实敌人 AI、
帧级对齐真机（需要日志反推校准）。

---

## 文档

- [`docs/01-调研.md`](docs/01-调研.md) 调研报告（机制 / 数据源 / 前作 / 风险）
- [`docs/02-计划.md`](docs/02-计划.md) 执行计划与验收标准
- [`docs/03-进展.md`](docs/03-进展.md) 逐阶段进展与**偏离说明**
- [`docs/04-验收报告.md`](docs/04-验收报告.md) 目标逐条验收证据
- `CHANGELOG.md` 版本变化与已知限制

---

## 免责声明

非官方粉丝作品，与 Hypergryph / Gryphline 无关，不含任何游戏资源。
详见 [LICENSE](LICENSE) 的 NOTICE 段。
