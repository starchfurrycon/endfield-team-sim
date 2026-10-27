# LLM 自更新能力审计（v3.7.0）

结论先说：**能适配"新技能文本 → 机制标签"，不能凭空适配"新数值/新机制公式"。**
下面每一条都有可复现的验证方式。

## 1. 能力矩阵

| 能力 | 机制 | 验证 |
|---|---|---|
| 接任意 OpenAI 兼容 API | 界面 / `llm` 子命令 / `ENDFIELD_LLM_*`；key 只存本机、只回掩码 | `run.py llm --self-test`；`GET /api/llm` 无明文 key |
| 新技能文本适配 | 规则先跑 → 不确定的交给 LLM → schema（封闭枚举）→ 证据校验 → 合并 | `tests/test_llm_audit.py::test_new_text_gets_semantics`（合成"未来干员/过载"文本，写入 `element=电磁, trigger=on_status`） |
| 拒绝幻觉 | evidence 必须是原文逐字子串；不合规修复一次后丢弃 | `test_bad_evidence_is_rejected` |
| 不产数值 | `gives_sp/gives_ult` 由代码强制清空 | `test_new_text_gets_semantics` 断言为空 |
| 无 API 自存活 | 规则通道独立工作；LLM 不可用不影响构建/推荐 | `test_no_api_falls_back_to_rules`、`selftest` 的 SKIP 项 |
| 闭环 | 复核 → `data/reports/llm-review.md` → 重载数据 → 界面自动重算 | 界面「运行复核」；`update --llm`；失败自动回到规则结果 |
| 自检 | 配置/掩码/提示词/规则/缓存/联网标注 6 类检查 | `run.py llm --self-test` 或界面 `LLM → 自检` |

## 2. 一次真实自检的输出（未配置 API 时）

```
PASS  配置读取：deepseek-chat
PASS  密钥掩码：未设置 key
PASS  提示词 semantics_system.md：4338 B
PASS  提示词 semantics_repair.md：617 B
PASS  提示词 mechanics_system.md：1276 B
PASS  规则兜底：trigger=on_hit_taken
PASS  缓存：5 条 / 5 KB
SKIP  LLM 标注：未配置 API（跳过联网）
自检通过
```

配置 API 后，会多出 `LLM 标注 / 证据校验 / 不产数值 / 耗时` 四项，
并且用你的一条真实技能文本发一次请求（结果进缓存，不重复计费）。

## 3. 边界（写清楚，不吹）

- **能**：新干员/新技能的触发条件、元素、异常、定位、是否 DoT、是否依赖受击；
- **不能**：新伤害公式、新 buff 叠加规则、数值倍率 —— 这些仍要改引擎；
  引擎不认识的会进 `unmodeled` 与 `data-quality.md`，不会被假装算对；
- 免费/弱模型对"具体角色+具体装备"不可控（图标那次已经踩过），
  建议用 DeepSeek / OpenAI 级别的模型；
- 证据校验偏严：模型若"改写"原文而不是引用，会被丢弃并回退规则 —— 这是有意的。

## 4. 如何自己确认（3 分钟）

```bash
python run.py llm --base https://api.deepseek.com/v1 --model deepseek-chat \
                  --key sk-xxx --enable
python run.py llm --self-test          # 六类检查 + 一次真实标注
python run.py llm --apply --mode gaps --limit 5   # 小样本复核，看报告
```

界面里：`LLM` → 填接口/模型/Key → `测试连接` → `自检` → `运行复核`。
复核完成后会自动刷新数据并重算当前队伍。
