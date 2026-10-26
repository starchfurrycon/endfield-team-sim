# 随心配（Suixinpei）· v3.4.0

《明日方舟：终末地》的**自动配队 + 输出轴**桌面工具。

界面按**终末地官网的设计语言**做的：奶油纸底、墨黑字、信号黄强调、全直角工业编辑风；
图标是埃特拉的 Q 版大头（内置资产，圆形；见 `desktop/assets/ICON.md`）。

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

### 方式 A：下载桌面版（推荐）

从 Releases 下载 `suixinpei-3.4.0-win64.zip`，解压后双击：

```
随心配.exe
```

首次启动会在后台下载游戏数据并构建（需要联网，约 1~3 分钟），
窗口里显示 `INITIALIZING` 进度；完成后自动进入配队界面。

> 数据在程序同目录的 `data/`（日志 `data/logs/server.log`）。
> 想更新游戏版本：删掉 `data/` 重开。
> 想换端口/数据目录：设置 `EF_PORT` / `EF_HOME`。

### 方式 B：从源码运行

```bash
python run.py update    # 一条命令更新数据：sync → build → 自检
python run.py serve --port 8777        # 浏览器 UI（无桌面壳）
python run.py llm --test               # 测试已配置的 LLM API

python run.py sync      # 只刷新数据源
python run.py build     # 只重建数据集
python run.py check     # 完整度 + 常量来源 + 数据自检

python run.py team --must 陈千语 --ban 管理员 --top 3 --duration 40
python run.py axis --team 陈千语,佩丽卡,昼雪,余烬 --mode both --duration 40
python run.py axis --team 陈千语 --equip output --weapon 1   # 带装备/武器预设
```

桌面壳（开发模式）：

```bat
cd desktop
npm install            :: 卡住就设 ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm start              :: 需要本机 Python
```

打包桌面版：`python tools/package_desktop.py`（需要本机已装 electron 与 rcedit）。

Python 3.10+，**无第三方依赖**（仅标准库）。

---

## 界面

- **Endfield 风格**：黑底 + 终末地黄、切角面板、Latin 微标签、自定义标题栏
- **头像化**：干员列表、队伍卡、水位轨、对比视图都用头像 + 数字
- 点干员：**一次 = 必须包含，两次 = 禁止，三次 = 清除**；顶部色点按元素过滤
- 每张卡切换**冷 / 热**两条轴；轴是**甘特图**（白竖线 = 命中帧）
- **水位轨**：技力与每人终结技能量曲线，悬停读数
- **对比**：同一队两套配置并排，给总伤差值与百分比
- **装备/武器推荐**：每人 3 把武器 + 2 套装备（输出向 / 功能向）
- 细节（动作表 / 警告 / 未建模项）收进卡片的 `ⓘ`
- 首次启动有**构建进度**，失败可重试
- 工程结构与状态机见 [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 本地 API（UI 用的就是这些）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 状态机快照：构建状态、进度、`llm_run`、`events` |
| GET | `/api/meta` | 干员列表 + 常量来源 + 预设；构建中返回 503 |
| GET | `/api/axis` | 单队一条轴：`team / mode / duration / enemy / equip / weapon` |
| POST | `/api/team` | 约束配队 Top-N |
| POST | `/api/compare` | 两套配置并排对比（`a` / `b` 各一份配置） |
| POST | `/api/loadout` | 每人的武器 3 选 + 装备 2 套 |
| GET | `/api/llm` | LLM 配置（密钥掩码）+ 复核任务状态 |
| POST | `/api/llm` | 保存 LLM 配置（`base/model/key/enabled`） |
| POST | `/api/llm/test` | 测试连接 |
| POST | `/api/llm/run` | 触发一次复核（`mode=gaps|all`、`limit`） |

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

## LLM 自更新（可选，自带 API）

游戏更新后，真正会变的不只是"多了个干员"，还有**新技能文本的机制含义**
（触发条件、元素、异常、角色定位）。这部分规则层覆盖不全，LLM 正好补位。

> 边界说清楚：LLM 提供的是**可验证的语义自更新**；
> "新伤害机制 / 新 buff 叠加规则"仍然要改引擎——这类会显式列进 `unmodeled`，
> 不会被假装算对。本项目不承诺"完全不看就永远正确"。

### 配置（三选一）

1. **界面**：右上角 `LLM` → 填接口 / 模型 / API Key → 保存 → 测试连接。
2. **命令行**：
   ```bash
   python run.py llm --base https://api.deepseek.com/v1 \
                      --model deepseek-chat --key sk-xxx --enable
   python run.py llm --test
   ```
3. **环境变量**（CI 用）：`ENDFIELD_LLM_BASE` / `ENDFIELD_LLM_MODEL` /
   `ENDFIELD_LLM_API_KEY` / `ENDFIELD_LLM_ENABLED`。

密钥只写在本机 `data/llm.json`（已 gitignore），接口只回传掩码。

### 更新流程

```bash
python run.py update --llm        # sync → build → 规则 + LLM 复核 → 自检
python run.py llm --apply --mode gaps --limit 20   # 只复核"不确定"的技能
python run.py llm --apply --mode all --dry-run     # 全量试跑，不写回
```

界面里也能直接点「运行复核」：范围可选「仅不确定（省 token）/ 全部」，
跑完刷新数据并给出统计（采纳/拒绝、改写数、tokens、成本估算）。

### 它到底改了什么

每次复核都会写 [`data/reports/llm-review.md`](data/reports/llm-review.md)：

- **被改写技能**表：字段、原值 → 新值、置信度、**原文证据**；
- **冲突**：规则与 LLM 不一致时保留规则，交给人判；
- **仍 unknown / 低置信度**：LLM 也没把握的部分；
- 怎么复核、怎么改 `curated.json`、什么时候该改提示词。

硬约束（代码强制，不靠模型自觉）：

- 不接收任何数值（技力/能量字段会被清空）；
- 输出必须过 schema（封闭枚举）；
- `evidence` 必须是原文逐字子串，否则重试一次后丢弃；
- 没文本的技能不调用（省 token，也不瞎猜）。

### 提示词

提示词是仓库资产，不是藏在代码里的字符串：

- [`semantics_system.md`](src/endfield/semantics/prompts/semantics_system.md)
- [`semantics_repair.md`](src/endfield/semantics/prompts/semantics_repair.md)（校验失败修复）
- [`mechanics_system.md`](src/endfield/semantics/prompts/mechanics_system.md)（未知黑板键归类）
- [`examples.json`](src/endfield/semantics/prompts/examples.json)（5 个真实技能 few-shot）
- 设计说明与评估方法：[`prompts/README.md`](src/endfield/semantics/prompts/README.md)

改动提示词语义时必须把 `llm.py:PROMPT_VERSION` +1，否则缓存会复用旧结果。

### 隐私与成本

- 只发送**技能描述文本**（没有账号、没有本机路径、没有数据表）；
- 默认 `gaps` 模式只问不确定的技能，一个版本通常 10~30 次调用；
- 缓存按（提示词版本, 模型, 输入）内容寻址，同一技能只计费一次；
- 在 `data/llm.json` 填 `price_in` / `price_out`（元/百万 token）后会显示估算成本。

---

## 文档

- [`ARCHITECTURE.md`](ARCHITECTURE.md) 工程结构 / 状态机 / 打包 / 扩展点
- [`docs/01-调研.md`](docs/01-调研.md) 调研报告（机制 / 数据源 / 前作 / 风险）
- [`docs/02-计划.md`](docs/02-计划.md) 执行计划与验收标准
- [`docs/03-进展.md`](docs/03-进展.md) 逐阶段进展与**偏离说明**
- [`docs/04-验收报告.md`](docs/04-验收报告.md) 目标逐条验收证据
- `CHANGELOG.md` 版本变化与已知限制

---

## 免责声明

非官方粉丝作品，与 Hypergryph / Gryphline 无关，不含任何游戏资源。
详见 [LICENSE](LICENSE) 的 NOTICE 段。
