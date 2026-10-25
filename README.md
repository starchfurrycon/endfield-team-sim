# 终末地配队模拟（Endfield Team Sim）· v0.1.0-beta

《明日方舟：终末地》的**自动配队 + 输出轴**工具。

给定「必须包含 / 禁止包含 / 可选池 / 战斗场景」，输出：

- **Top-N 配队**（含评分与理由）
- 每队的 **冷启动轴**（终结技能量 0）与 **热启动轴**（终结技能量满）
- 逐动作的**帧级时间轴**（30 fps）与命中时刻
- 每个数字的**来源**，以及**未建模项清单**

> ⚠️ 定位：**排序器 + 可解释建议**，不是"伤害真值机"。
> 绝对伤害明显偏低（未建模 buff、元素异常、武器），请只用它做**相对比较**。
> 详见 [`CHANGELOG.md`](CHANGELOG.md) 的「已知限制」。

---

## 快速开始

### 方式 A：下载测试版（推荐给只想起用的）

从 Releases 下载 `endfield-team-sim-0.1.0-beta-win64.zip`，解压后双击：

```
endfield-server.exe
```

首次启动会**自动从社区数据仓构建数据集**（需要联网，约 1~3 分钟），
完成后浏览器会自动打开 `http://127.0.0.1:8777/`。

> 之后每次启动都会直接复用本地数据；想更新游戏版本时删掉 `data/` 重开即可。

### 方式 B：从源码运行

```bash
python run.py sync      # 拉取/刷新数据源（GitHub 拆包表 + BWIKI）
python run.py build     # 归一化 + 语义抽取 + 各类报告
python run.py check     # 数据完整度 + 常量来源

python run.py team --must 陈千语 --ban 管理员 --top 3 --duration 40
python run.py axis --team 陈千语,佩丽卡,昼雪,余烬 --mode both --duration 40
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
数据更新后重跑即可（`sync → build`）。

**做不到**：绝对 DPS 准确（缺 buff/异常/武器）、真实敌人 AI、
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
