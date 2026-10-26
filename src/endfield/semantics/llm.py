"""LLM 语义增强（可选通道，用户自带 API）。

设计原则（和 docs/02-计划.md §1.2 一致，但更严格）：
  * LLM 只做**机制分类**，绝不产出数值倍率；
  * 输出必须过 schema 校验 + **证据校验**（evidence 必须是原文子串）；
  * 校验失败先让模型按错误清单**修复一次**，仍失败就丢弃，回退规则结果；
  * 内容寻址缓存（prompt 版本 + 模型 + 输入），同一技能不重复计费；
  * 未配置 API 时全流程降级，不报错（`available()` 为 False）。

提示词在 `prompts/*.md`，示例在 `prompts/examples.json`。
改动提示词语义时必须 bump `PROMPT_VERSION`，否则旧缓存会被错误复用。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .. import config
from . import llm_config
from .schema import (ABNORMALS, ELEMENTS, ROLES, SEMANTICS_SCHEMA, SLOTS,
                     TARGETS, TRIGGERS, validate_semantics)

PROMPT_VERSION = "sem-v2"
PROMPTS = Path(__file__).with_name("prompts")

# 测试/自定义传输层：签名 (payload, cfg) -> dict（OpenAI 响应）
_TRANSPORT: Callable[[dict, dict], dict] | None = None


class LLMError(RuntimeError):
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def set_transport(fn: Callable[[dict, dict], dict] | None) -> None:
    global _TRANSPORT
    _TRANSPORT = fn


def available(cfg: dict | None = None) -> bool:
    return llm_config.available(cfg)


# ---------------------------------------------------------------- 文本/提示词

def clean_text(text: str | None) -> str:
    """去掉 wiki 模板标记，保留可引用原文。"""
    if not text:
        return ""
    s = str(text)
    # {{术语|击飞}} → 击飞；[[A|B]] → B；{{X}}/[[X]] → X
    s = re.sub(r"\{\{[^{}]*\|([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = s.replace("{{", "").replace("}}", "").replace("[[", "").replace("]]", "")
    return " ".join(s.split())


@lru_cache(maxsize=1)
def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _schema_text() -> str:
    lines = [
        f'"slot": "{"|".join(SLOTS)}"',
        f'"trigger": "{"|".join(TRIGGERS)}"',
        '"trigger_params": {"hp_below_pct": "number|null", "requires_status": "string|null", "window_s": "number|null"}',
        f'"element": "{"|".join([e for e in ELEMENTS if e])}|null"',
        f'"abnormal": "{"|".join([a for a in ABNORMALS if a])}|null"',
        f'"target": "{"|".join(TARGETS)}"',
        f'"role": "{"|".join(ROLES)}"',
        '"duration_s": "number|null"',
        '"is_dot": "bool"',
        '"needs_enemy_hit": "bool"',
        '"heals": "bool"',
        '"shields": "bool"',
        '"tags": "[string]"',
        '"evidence": "[原文逐字片段, 1~3 条]"',
        '"confidence": "0~1"',
        '"method": "llm"',
        '"notes": "[string]"',
    ]
    return "{\n  " + ",\n  ".join(lines) + "\n}"


def _examples_text() -> str:
    try:
        data = json.loads(_read("examples.json"))
    except Exception:  # noqa: BLE001
        return ""
    blocks = []
    for i, row in enumerate(data, 1):
        blocks.append(
            f"### 示例 {i}\n输入：{json.dumps(row['input'], ensure_ascii=False)}\n"
            f"输出：{json.dumps(row['output'], ensure_ascii=False)}")
    return "\n\n".join(blocks)


def system_prompt() -> str:
    tpl = _read("semantics_system.md")
    return (tpl.replace("{SCHEMA}", _schema_text())
               .replace("{EXAMPLES}", _examples_text()))


def repair_prompt(payload: dict, previous: dict | str, errors: list[str]) -> str:
    tpl = _read("semantics_repair.md")
    return (tpl.replace("{INPUT}", json.dumps(payload, ensure_ascii=False))
               .replace("{PREVIOUS}", json.dumps(previous, ensure_ascii=False)
                        if not isinstance(previous, str) else previous)
               .replace("{ERRORS}", "\n".join(f"- {e}" for e in errors))
               .replace("{SCHEMA}", _schema_text()))


def mechanics_prompt(items: list[dict]) -> str:
    return _read("mechanics_system.md") + "\n\n## 输入\n\n" + json.dumps(
        {"items": items}, ensure_ascii=False)


# ---------------------------------------------------------------- HTTP

def _payload(cfg: dict, messages: list[dict], max_tokens: int | None,
             json_mode: bool) -> dict:
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0)),
        "max_tokens": int(max_tokens or cfg.get("max_tokens", 2048)),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def _http_call(payload: dict, cfg: dict) -> dict:
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['key']}",
                 "User-Agent": "endfield-team-sim/2.5"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout_s", 90))) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read()[:500].decode("utf-8", "replace")
        kind = "auth" if e.code in (401, 403) else (
            "rate" if e.code == 429 else "http")
        raise LLMError(f"HTTP {e.code}: {body}", kind) from e
    except urllib.error.URLError as e:
        raise LLMError(f"网络错误: {e.reason}", "network") from e
    except TimeoutError as e:
        raise LLMError("请求超时", "timeout") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"响应不是 JSON: {e}", "bad_response") from e


def _call(payload: dict, cfg: dict) -> dict:
    """带一次重试（网络/限流/5xx）的调用，返回 {content, usage}。"""
    last: LLMError | None = None
    for attempt in range(2):
        try:
            resp = (_TRANSPORT or _http_call)(payload, cfg)
            try:
                content = resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"响应结构异常: {e}", "bad_response") from e
            return {"content": content, "usage": resp.get("usage") or {}}
        except LLMError as e:
            last = e
            if e.kind in ("auth", "bad_response"):
                raise
            if attempt == 0:
                time.sleep(1.2)
    raise last or LLMError("unknown")


def _json_from_text(text: str) -> dict:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        s = s.split("\n", 1)[1] if s.lower().startswith(("json", "json\n")) else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        raise LLMError("输出里没有 JSON 对象", "invalid_json")
    try:
        obj = json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"输出 JSON 解析失败: {e}", "invalid_json") from e
    if not isinstance(obj, dict):
        raise LLMError("输出不是 JSON 对象", "invalid_json")
    return obj


def chat(messages: list[dict], cfg: dict | None = None, *,
         max_tokens: int | None = None, json_mode: bool = True) -> dict:
    cfg = cfg or llm_config.load()
    if not llm_config.available(cfg):
        raise LLMError("LLM 未配置（缺少 key 或未启用）", "unavailable")
    payload = _payload(cfg, messages, max_tokens, json_mode)
    raw = _call(payload, cfg)
    return {"text": raw["content"], "usage": raw["usage"]}


# ---------------------------------------------------------------- 校验

def _clamp(v, lo=0.0, hi=1.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, x))


def _evidence_check(sem: dict, text: str) -> list[str]:
    ev = sem.get("evidence") or []
    if not isinstance(ev, list) or any(not isinstance(x, str) for x in ev):
        return ["evidence 必须是字符串数组"]
    errs = []
    for quote in ev:
        q = str(quote).strip()
        if q and q not in text:
            errs.append(f"evidence 不是原文子串: {q[:30]}")
    strong = (sem.get("element") not in (None,)
              or sem.get("abnormal") not in (None,)
              or sem.get("trigger") not in (None, "none", "unknown")
              or sem.get("role") not in (None, "unknown"))
    if strong and not [x for x in ev if str(x).strip()]:
        errs.append("有实质性判断但 evidence 为空")
    return errs


def _sanitize(sem: dict, slot: str, text: str) -> dict:
    keep = ("trigger", "trigger_params", "element", "abnormal", "target", "role",
            "duration_s", "is_dot", "needs_enemy_hit", "gives_sp", "gives_ult",
            "heals", "shields", "tags", "evidence", "confidence", "notes")
    out = {k: sem.get(k) for k in keep if k in sem}
    out["slot"] = slot
    out["method"] = "llm"
    # 硬约束：不接收任何数值（数值必须来自数据表）
    out["gives_sp"] = None
    out["gives_ult"] = None
    if out.get("trigger_params") is None:
        out["trigger_params"] = {}
    out["tags"] = [str(t) for t in (out.get("tags") or [])][:8]
    out["evidence"] = [str(e) for e in (out.get("evidence") or [])][:5]
    out["notes"] = [str(n) for n in (out.get("notes") or [])][:6]
    out["confidence"] = round(_clamp(out.get("confidence", 0.5)), 2)
    out["text_zh"] = text or None
    return out


# ---------------------------------------------------------------- 缓存

def _cache_path(payload: dict, model: str) -> Path:
    h = hashlib.sha256(json.dumps(
        {"v": PROMPT_VERSION, "model": model, "input": payload},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
    return config.LLM_CACHE / f"sem_{h}.json"


def _cache_load(p: Path, text: str) -> dict | None:
    try:
        row = json.loads(p.read_text(encoding="utf-8"))
        sem = row.get("sem") or {}
        ok, errs = validate_semantics(sem)
        if not ok:
            return None
        errs = _evidence_check(sem, text)
        return sem if not errs else None
    except Exception:  # noqa: BLE001
        return None


def _cache_save(p: Path, sem: dict, payload: dict, usage: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "prompt_version": PROMPT_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": payload, "sem": sem, "usage": usage,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 标注

def _skill_payload(skill: dict) -> dict:
    sem = skill.get("semantics") or {}
    return {
        "operator": skill.get("operator") or sem.get("operator") or "",
        "slot": skill.get("slot"),
        "name": skill.get("name_zh") or "",
        "element_hint": skill.get("element"),
        "text": clean_text(sem.get("text_zh")),
    }


def annotate_skill(skill: dict, *, cfg: dict | None = None,
                   use_cache: bool = True, allow_repair: bool = True,
                   usage_out: dict | None = None) -> dict | None:
    """标注一个技能。失败返回 None（调用方回退规则结果）。"""
    cfg = cfg or llm_config.load()
    if not llm_config.available(cfg):
        return None
    payload = _skill_payload(skill)
    text = payload["text"]
    if not text:
        return None
    cache = _cache_path(payload, cfg["model"])
    if use_cache and cache.exists():
        cached = _cache_load(cache, text)
        if cached:
            if usage_out is not None:
                usage_out["cache"] = usage_out.get("cache", 0) + 1
            return cached

    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    try:
        res = chat(messages, cfg)
    except LLMError:
        return None
    usage = res.get("usage") or {}
    if usage_out is not None:
        usage_out["prompt_tokens"] = usage_out.get("prompt_tokens", 0) + int(
            usage.get("prompt_tokens") or 0)
        usage_out["completion_tokens"] = usage_out.get("completion_tokens", 0) + int(
            usage.get("completion_tokens") or 0)
        usage_out["calls"] = usage_out.get("calls", 0) + 1

    sem = None
    errors: list[str] = []
    try:
        sem = _sanitize(_json_from_text(res["text"]), payload["slot"], text)
        ok, errs = validate_semantics(sem)
        errors = ([] if ok else errs) + _evidence_check(sem, text)
    except LLMError as e:
        errors = [str(e)]

    if errors and allow_repair:
        if usage_out is not None:
            usage_out["repaired"] = usage_out.get("repaired", 0) + 1
        messages = messages + [
            {"role": "assistant", "content": res["text"]},
            {"role": "user", "content": repair_prompt(payload, sem or {}, errors)},
        ]
        try:
            res2 = chat(messages, cfg)
            u2 = res2.get("usage") or {}
            if usage_out is not None:
                usage_out["prompt_tokens"] = usage_out.get("prompt_tokens", 0) + int(
                    u2.get("prompt_tokens") or 0)
                usage_out["completion_tokens"] = usage_out.get("completion_tokens", 0) + int(
                    u2.get("completion_tokens") or 0)
                usage_out["calls"] = usage_out.get("calls", 0) + 1
            sem = _sanitize(_json_from_text(res2["text"]), payload["slot"], text)
            ok, errs = validate_semantics(sem)
            errors = ([] if ok else errs) + _evidence_check(sem, text)
        except LLMError as e:
            errors = [str(e)]

    if errors or sem is None:
        return None
    sem["method"] = "llm"
    _cache_save(cache, sem, payload, usage)
    return sem


# ---------------------------------------------------------------- 机制归类

MECH_CATEGORIES = ["damage_mult", "atk_percent", "element_damage",
                   "slot_efficiency", "crit", "cooldown", "sp_recover",
                   "energy_gain", "heal_shield", "duration", "condition", "other"]


def classify_mechanics(items: list[dict], *, cfg: dict | None = None,
                       usage_out: dict | None = None) -> list[dict] | None:
    """让 LLM 给未知黑板 key 归类（只写入报告，不改引擎）。"""
    cfg = cfg or llm_config.load()
    if not llm_config.available(cfg) or not items:
        return None
    try:
        res = chat([{"role": "user", "content": mechanics_prompt(items)}], cfg,
                   max_tokens=2048)
        usage = res.get("usage") or {}
        if usage_out is not None:
            usage_out["prompt_tokens"] = usage_out.get("prompt_tokens", 0) + int(
                usage.get("prompt_tokens") or 0)
            usage_out["completion_tokens"] = usage_out.get("completion_tokens", 0) + int(
                usage.get("completion_tokens") or 0)
            usage_out["calls"] = usage_out.get("calls", 0) + 1
        obj = _json_from_text(res["text"])
    except LLMError:
        return None
    out = []
    for row in obj.get("items") or []:
        if not isinstance(row, dict) or not row.get("key"):
            continue
        cat = str(row.get("category") or "other")
        out.append({
            "key": str(row["key"]),
            "category": cat if cat in MECH_CATEGORIES else "other",
            "modeled_by_engine": bool(row.get("modeled_by_engine")),
            "meaning": str(row.get("meaning") or "")[:120],
            "confidence": round(_clamp(row.get("confidence", 0.3)), 2),
        })
    return out


# ---------------------------------------------------------------- 运维

def test_connection(cfg: dict | None = None) -> dict:
    cfg = cfg or llm_config.load()
    if not llm_config.available(cfg):
        return {"ok": False, "error": "未启用或缺少 API key"}
    t0 = time.time()
    try:
        res = chat([
            {"role": "system", "content": "只输出 JSON：{\"ok\": true}"},
            {"role": "user", "content": "ping"},
        ], cfg, max_tokens=32)
        obj = _json_from_text(res["text"])
        return {"ok": bool(obj.get("ok", True)), "model": cfg["model"],
                "latency_ms": int((time.time() - t0) * 1000),
                "usage": res.get("usage") or {}}
    except LLMError as e:
        return {"ok": False, "error": f"{e.kind}: {e}",
                "latency_ms": int((time.time() - t0) * 1000)}


def cache_stats() -> dict:
    files = list(config.LLM_CACHE.glob("sem_*.json")) if config.LLM_CACHE.exists() else []
    size = sum(f.stat().st_size for f in files)
    return {"files": len(files), "bytes": size}


def clear_cache() -> int:
    n = 0
    for f in config.LLM_CACHE.glob("sem_*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def describe() -> str:
    cfg = llm_config.load()
    pub = llm_config.public(cfg)
    st = cache_stats()
    return (f"LLM: {'可用' if llm_config.available(cfg) else '未配置/未启用'}\n"
            f"  base={pub['base']}  model={pub['model']}  "
            f"key={pub['key_masked'] or '（空）'}\n"
            f"  prompt={PROMPT_VERSION}  缓存 {st['files']} 条 / "
            f"{st['bytes'] / 1024:.0f} KB\n"
            f"  trigger 枚举: {', '.join(TRIGGERS)}\n"
            f"  schema 字段: {', '.join(SEMANTICS_SCHEMA['properties'])}")
