上一次输出没有通过校验。请**只修正列出的错误**，其余字段与证据保持原样，
重新输出完整 JSON（不要解释）。

## 原始输入

{INPUT}

## 上一次输出

{PREVIOUS}

## 校验错误

{ERRORS}

## 修正要求

- 枚举字段必须严格用下方枚举值；不确定就填 `"unknown"` / `null`。
- `evidence` 必须是原文子串；如果某个判断没有原文支撑，就把对应字段改成 `null`/`unknown`
  并把 `confidence` 降到 0.4 以下。
- 不要新增字段，不要删除必填字段（slot/trigger/confidence/method）。

## 字段枚举

{SCHEMA}
