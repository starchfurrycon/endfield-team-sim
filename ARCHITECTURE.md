# 架构说明（v3.1.0）

这份文档描述**软件怎么跑起来、状态怎么流转、代码边界在哪**。
产品边界（数值能不能当真）在 README / CHANGELOG，这里只讲工程结构。

---

## 1. 分层

```
                    ┌─────────────────────────────┐
   数据源            │ data/                       │
   GitHub 拆包表 ───►│  http.py  sources.py        │
   BWIKI / Talos ───►│  github.py wiki.py avatars  │
                     │  build.py  coverage quality │
                     └──────────────┬──────────────┘
                                    │ data/normalized/*.json
                     ┌──────────────▼──────────────┐
   语义              │ semantics/                  │
                     │  rules.py（主）             │
                     │  llm.py + prompts/（可选）   │
                     │  schema.py（封闭枚举/证据）  │
                     │  apply.py（合并 + 复核报告） │
                     └──────────────┬──────────────┘
                                    │ operator.skills[*].semantics
                     ┌──────────────▼──────────────┐
   仿真              │ engine/                     │
                     │  battle.py（帧级状态机）     │
                     │  reactions.py damage.py     │
                     └──────────────┬──────────────┘
                                    │ Result(timeline / stats / samples)
                     ┌──────────────▼──────────────┐
   搜索              │ search/                     │
                     │  rotations.py teams.py      │
                     │  loadout.py（装备/武器推荐） │
                     └──────────────┬──────────────┘
                                    │
        ┌───────────────────────────┴──────────────────────────┐
        │ web/（状态机 + API + HTTP）                            │
        │  state.py 数据/LLM 状态机    jobs.py 后台任务           │
        │  api.py   纯函数端点         server.py 路由 + 静态页     │
        └───────────────────────────┬──────────────────────────┘
                                    │ http://127.0.0.1:<port>
                     ┌──────────────▼──────────────┐
   界面              │ desktop/                    │
                     │  index.html（单页 UI）       │
                     │  main.js（Electron 壳）      │
                     └─────────────────────────────┘
```

## 2. 两条链路

### 2.1 数据链路（离线可重复）

```
sync  →  build  →  semantics(rule)  →  [semantics(llm)]  →  reports
```

- `build` 只做「表 → 归一化 JSON」，任何失败不覆盖上一版；
- `semantics` 规则层永远先跑；LLM 只在规则不确定时补位（见 §4）；
- 数据自检 `data-quality.md`、LLM 复核 `llm-review.md` 与数据一起产出。

### 2.2 运行时链路

```
Electron 壳启动 → 拉起后端 → 等 /api/status → 加载页面 → 页面轮询状态
```

- 后端**先监听、后在后台构建**，所以首启能看到进度而不是白屏；
- 页面只依赖 `/api/status` + `/api/llm` 两个状态源，不做本地推断。

## 3. 状态机（唯一真相）

`web/state.py` 里有两个状态机，HTTP 层、后台任务、界面都读同一份快照。

```
数据：
  idle ──start──► building ──完成──► ready
                    │ 错误
                    ▼
                  error ──start──► building

LLM 复核（前置：data == ready）：
  idle ──start──► running ──完成──► done
                    │ 错误
                    ▼
                  error ──start──► running
```

不变量（都有单测 `tests/test_state.py`）：

1. 同一时间只有一个数据构建任务；重复 start 返回 False。
2. 构建进度只在 `building` 状态下被接受（不会把 ready 改回 42%）。
3. 数据未 ready 时发起 LLM 复核 → 抛错（HTTP 503/500，界面提示）。
4. 同一时间只有一个 LLM 复核任务。
5. LLM 完成后立即失效数据集缓存（语义变了，必须重新加载）。
6. 每次转移写入事件日志（`/api/status` 里的 `events`，最近 20 条）。

## 4. LLM 层边界（为什么不会算错数）

```
技能文本 → 规则层 ──已确定──► 直接用
              │ 不确定（未知触发/缺元素/低置信度）
              ▼
        LLM 标注 → schema 校验 → 证据校验（原文子串）
              │ 失败
              ▼
        修复一次 → 仍失败 → 丢弃，保留规则结果
```

- LLM 只填**类别字段**；`gives_sp/gives_ult` 等数值字段被代码强制清空；
- 合并策略：规则为主、LLM 补空；冲突保留规则并写进复核报告；
- 缓存键 =（提示词版本, 模型, 输入），改提示词必须 bump `PROMPT_VERSION`。

## 5. Web 层

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `state.py` | 状态与转移、数据集缓存、事件 | 不读网络、不跑构建 |
| `jobs.py` | 构建/复核任务编排 | 不碰 HTTP、不写页面 |
| `api.py` | 纯函数端点（meta/axis/team/compare/loadout/llm） | 不碰 socket |
| `server.py` | 路由、静态页、头像、启动对齐 | 不含业务逻辑 |

新增一个接口的标准动作：`api.py` 写函数 + `server.py` 加一条路由 + 测试直接调 `api.*`。

## 6. 桌面壳

`desktop/main.js` 启动状态机：

```
starting ──► server_up ──► page_loaded
    │ 失败
    ▼
 failed（显示日志尾部 + 重试提示）
```

- 数据目录：`EF_HOME`（默认 exe 旁边的 `data/`）；端口：`EF_PORT` 或自动挑空闲端口；
- 后端 stdout/stderr 写入 `data/logs/server.log`，同时保留内存尾部用于错误页；
- 退出时 `taskkill /T /F` 杀掉整棵进程树（避免残留 server）；
- 单实例锁：重复启动只聚焦已有窗口。

### 打包产物（`tools/package_desktop.py`）

```
随心配-3.0.0-win64/
├─ 随心配.exe            Electron（rcedit 写图标/版本信息）
├─ resources/app/             main.js / index.html / package.json / assets
├─ resources/server/          endfield-server.exe（PyInstaller onefile）
└─ README.txt / LICENSE.txt
```

`resources/default_app.asar` 被删除；locales 只保留中英文。

## 7. 质量门槛

- `python -m unittest discover -s tests`：状态机、引擎不变量、语义、LLM 校验、API 契约；
- `python run.py check`：覆盖 + 常量来源 + 数据自检；
- `python run.py llm --test`：用户 API 连通性；
- 每次构建都产出 `data-quality.md`；每次 LLM 复核都产出 `llm-review.md`。

## 8. 扩展点（新内容来了改哪里）

| 需求 | 改这里 |
|---|---|
| 新数据表/字段 | `data/build.py` + `model.py`，并补 `check` 覆盖 |
| 新伤害机制 | `engine/battle.py`（`_hit_damage` / 新乘区）+ `unmodeled` 声明 |
| 新触发条件/元素词 | `semantics/rules.py`（规则优先，LLM 兜底） |
| 提示词策略 | `semantics/prompts/*` + bump `PROMPT_VERSION` |
| 新界面视图 | `desktop/index.html` + `web/api.py` 端点 |
| 新发行形态 | `tools/package_desktop.py` / `packaging/entry.py` |


## 9. 教学生成（v3.5+）

`report/guide.py` 把一条轴翻译成「怎么打」：

- 输入：`Result.timeline` + `stats.energy_cap` + 技能语义（trigger / duration）
- 输出：`steps`（时间、角色、战技/连携/终结技、触发条件、×N）与 `tips`（最多 3 条）
- 约束：只讲轴上真实发生的事；切人未建模，只提醒"承诺帧内别切走"
- 入口：`/api/axis` 的 `guide` 字段，界面在轴下方渲染步骤带
