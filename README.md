# 终末地配队模拟（Endfield Team Sim）· v2.4.2

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

从 Releases 下载 `endfield-team-sim-2.4.2-win64.zip`，解压后双击：

```
endfield-server.exe
```

首次启动会**在后台从社区数据仓构建数据集**（需要联网，约 1~3 分钟）——
窗口里会显示**构建进度条**（阶段、百分比、已用时间），完成后自动进入界面。

> 之后每次启动都会直接复用本地数据；想更新游戏版本时删掉 `data/` 重开即可。
>
> 从 v2.3 升级：删掉 `data/` 重新构建一次，才会拿到头像与武器/装备中文名。

### 方式 B：从源码运行

```bash
python run.py update    # 一条命令更新数据：sync → build → 自检
python run.py serve --port 8777        # 本地 UI

python run.py sync      # 只刷新数据源
python run.py build     # 只重建数据集
python run.py check     # 完整度 + 常量来源 + 数据自检

python run.py team --must 陈千语 --ban 管理员 --top 3 --duration 40
python run.py axis --team 陈千语,佩丽卡,昼雪,余烬 --mode both --duration 40
python run.py axis --team 陈千语 --equip output --weapon 1   # 带装备/武器预设
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

- **头像化**：干员列表、队伍卡、水位轨、对比视图都用头像 + 数字，少看文字
- 点干员：**一次 = 必须包含，两次 = 禁止，三次 = 清除**；顶部色点按元素过滤
- 每张卡切换**冷 / 热**两条轴；轴是**甘特图**（白竖线 = 命中帧）
- **水位轨**：技力与每人终结技能量曲线，悬停读数
- **对比**：同一队两套配置并排，给总伤差值与百分比
- **装备/武器推荐**：每人 3 把武器 + 2 套装备（输出向 / 功能向）
- 细节（动作表 / 警告 / 未建模项）收进卡片的 `ⓘ`
- 首次启动有**构建进度**，失败可重试

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

## 数据更新（新干员 / 新装备 / 新机制）

一条命令：`python run.py update`（= `sync → build → 自检`）。

| 更新类型 | 自动程度 | 说明 |
|---|---|---|
| 新干员（`CharacterTable` + `SkillData` 齐全） | **全自动** | 帧、倍率、技力/能量消耗、稀有度、职业都来自表；缺 Wiki 文本时元素取客户端 `charTypeId`，并在自检报告里标"未独立核对" |
| 新武器 / 新装备 / 新套装 | **全自动** | 属性向量、套装黑板自动解析；新属性 id 先按 `AttributeMetaTable.iconName` 归类，归不上进"未定性"表 |
| 同干员异元素技能 | **半自动** | Wiki 有「战技属性」等字段就自动；没有则往 `src/endfield/data/curated.json` 补一条（必须写出处） |
| 新连携触发条件 | **半自动** | 规则层能识别常见触发词；未知条件落 `unknown`，结果里可见，**不会假装认识** |
| 新伤害机制（新 DoT / 异常 / 乘区） | **不自动** | 需要改引擎；每次输出的 `unmodeled` 会声明 |
| buff/debuff 体系 | **不自动** | 同上 |

每次 `build` / `check` 都会写 [`data/reports/data-quality.md`](data/reports/data-quality.md)，列出：

- 哪些干员的元素只有客户端来源（未独立核对）
- 哪些**技能元素是"按干员兜底"**（最容易算错，噗切娜就属于这类）
- 哪些属性 id 没被建模、哪些套装黑板 key 不认识、哪些武器条件型被动没算
- 人工修正（`curated.json`）清单与出处

**没有 LLM 也能正确降级**：不认识的东西不会假装认识，也不会脏进通用伤害乘区——
这是 v2.3.0 修过的教训（属性 id 一律当通用增伤，把装备收益夸大了 4 倍）。

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
