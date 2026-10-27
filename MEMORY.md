# 记忆文件 · 随心配（Suixinpei）

> 最后更新：2026-10-26 · 版本 **v3.15.0**
> 这份文件用来交接：项目是什么、代码在哪、怎么跑、边界在哪、下一步可以做什么。

## 1. 身份

- 软件名 / 仓库：**随心配** / `starchfurrycon/suixinpei`
- 定位：《明日方舟：终末地》的**配队 + 输出轴**桌面工具
- 技术栈：Python 3.10+（仅标准库） + 单页 HTML/JS + Electron 便携壳
- 最新发行：https://github.com/starchfurrycon/suixinpei/releases/tag/v3.15.0

| 产物 | 大小 | SHA256 |
|---|---|---|
| `suixinpei-3.15.0-win64.zip` | 114,516,887 | `7904182475B86D070D05CAA249B5329A1E90450B23DC349FAB0DB4342B185E6B` |
| `suixinpei-server-3.15.0-win64.zip` | 9,326,597 | `0CC3523C833B989E9E7C1D4265A8647A1D835EA27BB9FE79B34A14140551E646` |

## 2. 边界（先读这条）

- **是排序器 + 可解释建议**，不是伤害真值机；绝对 DPS 偏低，只用相对比较。
- 已建模：技能倍率、武器/装备、天赋/潜能、法术/物理异常、元素附着、失衡。
- 未建模：buff/debuff 体系、新伤害机制、真实敌人 AI、切人收益；
  这些进 `unmodeled` / `data-quality.md`，不会假装算对。
- LLM 只做「文本 → 机制标签」；数值、公式仍要改引擎。

## 3. 代码地图

```
run.py                  CLI 入口
src/endfield/
  config.py             路径 / 帧率 / 常量（EF_HOME 可改数据根）
  model.py              Operator / Skill / Dataset
  data/                 sync / build / 头像 / 自检报告
  semantics/            rules / llm / llm_config / schema / apply / selftest
    prompts/            semantics_system / repair / mechanics / examples
  engine/               battle（帧级状态机）/ reactions / damage
  search/               rotations（beam）/ teams / loadout
  report/               explain / guide（怎么打）
  web/                  state（状态机）/ jobs / api / server
desktop/
  index.html            单页 UI（内联 CSS/JS）
  main.js               Electron 壳（启动状态机、日志、EF_HOME/EF_PORT）
  assets/               icon_raw.png / icon.png / icon.ico / ICON.md
tools/                  package_desktop.py / make_icon.py / run_demo.py
tests/                  140 项（核心 / 状态机 / LLM / guide / 内容更新）
docs/                   01 调研 / 02 计划 / 03 进展 / 04 验收 / 05 LLM 审计
```

## 4. 常用命令

```bash
python run.py update             # sync → build → 自检（游戏更新后用）
python run.py update --llm       # 上面 + LLM 复核
python run.py serve --port 8777  # 浏览器模式
python run.py check              # 覆盖 / 常量 / 数据自检
python run.py team --must 陈千语 --top 3 --duration 40
python run.py axis --team 陈千语,大潘,萤石,埃特拉 --mode both

python run.py llm --base https://api.deepseek.com/v1 --model deepseek-chat \
                  --key YOUR_API_KEY --enable        # 配置（只存 data/llm.json）
python run.py llm --self-test                  # 能力自检（6 类）
python run.py llm --apply --mode gaps --limit 5

python -m unittest discover -s tests           # 140 项
python tools/package_desktop.py                # 需要 electron + rcedit
```

## 5. 状态机 / 工作流

- 数据：`idle → building → ready / error`（`web/state.py`，单任务、进度守卫）；
- LLM：`idle → running → done / error`（需 data=ready，完成即失效数据缓存）；
- 前端：选中即出轴（260ms 防抖 + 请求令牌）、空态示例队、localStorage 记忆、
  LLM 完成后自动刷新并重算；
- 桌面壳：`starting → server_up → page_loaded`，失败显示日志尾部。

## 6. LLM 能力（已审计）

- 自检 8 项：配置 / 掩码 / 三份提示词 / 规则兜底 / 缓存 / 联网标注（证据、数值、耗时）；
- 适配：新技能文本 → 元素/触发/异常（合成「未来干员/过载」测试通过）；
- 拒绝：证据非原文、枚举越界、含数值 → 修复一次后丢弃，回退规则；
- 自存活：没 API 或 API 失败时规则通道照常工作；
- 打包版注意：PyInstaller 必须带 `--add-data "...prompts;endfield/semantics/prompts"`，
  否则桌面版 LLM 失效（v3.7.0 踩过）；
- 细节：`docs/05-LLM能力审计.md`。

## 7. 近期版本

- v3.15.0 武器暴击/元素进公式 + 队伍评估带装备
- v3.14.1 装备/武器推荐重写（专武优先 + 整套 + 一队一个主 C）
- v3.13.1 演示静音（无配音/无配乐，仅短字幕）
- v3.13.0 短字幕演示 + BYO API 说明
- v3.12.0 默认约束版本隔离 + Top3 多样化 + 真实录屏演示
- v3.11.0 主控轨 / 可取消窗口 / 深链 + 宣传片（docs/media）
- v3.10.1 清掉界面说明文字（示例/勾选说明/报告路径）
- v3.10.0 推荐配队（0 约束推荐 Top N；必选/排除约束条；同名去重）
- v3.9.0 主控机制修正（只有主控普攻/重击；换人显式；docs/MECHANICS.md）
- v3.8.0 横向滚动轴（22px/秒，左侧 sticky）+ 修复甘特行头像被隐藏
- v3.7.0 轴标注 战/连/终 + LLM 自检 + 打包 prompts 修复
- v3.6.0 文案补字/收字
- v3.5.0 「怎么打」教学 + 自动出轴 + 工作流闭环
- v3.4.0 图标换埃特拉 Q 版大头
- v3.3.0 生成图标 + 官网组件语言（黄块/药丸/彩虹线）
- v3.2.0 黑栏纸面大字 + 纯圆图标
- v3.1.x Endfield 纸感 / 改名随心配
- v3.0.0 桌面化 + 状态机 + 清理

## 8. 本地清理说明

按要求清理了可再生成的东西：

- 删除：`dist/`（exe/截图）、`build/`、`release/`（zip 与暂存目录）、
  `desktop/node_modules/`、`data/`（运行时缓存）、`__pycache__/`、`.pytest_cache/`；
- 保留：源码、`docs/`、测试、`desktop/assets`（图标源）、README/CHANGELOG/
  ARCHITECTURE、`.git`、本文件。

下次运行：

```bash
pip install pyinstaller        # 只有打 exe 需要
python run.py update           # 会重新抓数据（约 1~3 分钟）
python run.py serve --port 8777
```

Electron 开发：`cd desktop && npm install && npm start`。

## 9. 后续可做

- 机制待确认项见 `docs/MECHANICS.md`（战技/终结技是否必须主控、连携是否占主控时间、处决/闪避）；
- 推荐支持"可选池/已拥有"模式（现在必选/排除已够用）；
- 更多机制：buff/debuff 叠加、武器条件型技能；
- 轴循环：把"循环轴"从展示（滚动）推进到真正的循环稳定性评分。
