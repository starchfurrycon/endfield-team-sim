"""LLM 通道自检：确认"能接、能标、能拒、能退化"。

输出一组检查项：ok=True 通过，False 失败，None 跳过（没配 API）。
`run.py llm --self-test` 与 `/api/llm/selftest` 共用。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import apply as sem_apply
from . import llm, llm_config, rules, schema

PROMPTS = ("semantics_system.md", "semantics_repair.md", "mechanics_system.md")
KNOWN_TEXT = "当主控干员受到攻击后，如果生命值低于60%可以发动，造成物理伤害。"


def _check(name: str, ok, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _sample_skill(ops: dict) -> dict | None:
    for op in ops.values():
        for sk in op.skills.values():
            text = ((sk.semantics or {}).get("text_zh") or "").strip()
            if len(text) >= 20:
                return {
                    "operator": op.name_zh,
                    "slot": sk.slot,
                    "name_zh": sk.name_zh,
                    "element": op.element,
                    "semantics": sk.semantics,
                }
    return None


def run(cfg: dict | None = None, transport=None) -> dict:
    cfg = cfg or llm_config.load()
    checks: list[dict] = []
    old = None

    # 1) 配置与掩码
    checks.append(_check("配置读取", isinstance(cfg, dict) and bool(cfg.get("model")),
                         str(cfg.get("model") or "（空）")))
    key = cfg.get("key") or ""
    if key:
        masked = llm_config.mask_key(key)
        checks.append(_check("密钥掩码", "•" in masked and key not in masked, masked))
    else:
        checks.append(_check("密钥掩码", True, "未设置 key"))

    # 2) 提示词资产
    base = Path(__file__).resolve().parent / "prompts"
    for name in PROMPTS:
        p = base / name
        size = p.stat().st_size if p.exists() else 0
        checks.append(_check(f"提示词 {name}", p.exists() and size > 400, f"{size} B"))

    # 3) 规则通道（无 LLM 也能工作）
    try:
        sem = rules.extract_skill({
            "slot": "combo_skill", "element": None,
            "semantics": {"text_zh": KNOWN_TEXT},
        })
        ok = bool(sem.get("trigger")) and sem.get("trigger") != "unknown"
        checks.append(_check("规则兜底", ok, f"trigger={sem.get('trigger')}"))
    except Exception as e:  # noqa: BLE001
        checks.append(_check("规则兜底", False, f"{type(e).__name__}: {e}"))

    # 4) 缓存
    try:
        st = llm.cache_stats()
        checks.append(_check("缓存", True, f"{st['files']} 条 / {st['bytes'] // 1024} KB"))
    except Exception as e:  # noqa: BLE001
        checks.append(_check("缓存", False, f"{type(e).__name__}: {e}"))

    # 5) 标注（有 API 才跑）
    if not llm_config.available(cfg):
        checks.append(_check("LLM 标注", None, "未配置 API（跳过联网）"))
    else:
        ops = sem_apply.load_operators()
        sample = _sample_skill(ops)
        if not sample:
            checks.append(_check("LLM 标注", None, "没有可用的技能文本"))
        else:
            if transport is not None:
                old = getattr(llm, "_TRANSPORT", None)
                llm.set_transport(transport)
            t0 = time.time()
            usage: dict = {}
            try:
                got = llm.annotate_skill(sample, cfg=cfg, use_cache=False,
                                         usage_out=usage)
            except Exception as e:  # noqa: BLE001
                got = None
                checks.append(_check("LLM 标注", False, f"{type(e).__name__}: {e}"))
            finally:
                if transport is not None:
                    llm.set_transport(old)
            if got is None:
                if not any(c["name"] == "LLM 标注" for c in checks):
                    checks.append(_check("LLM 标注", None,
                                         "被拒绝/无文本（可接受）"))
            else:
                ok, errs = schema.validate_semantics(got)
                text = sample["semantics"].get("text_zh") or ""
                ev_ok = bool(got.get("evidence")) and all(
                    e in text for e in got["evidence"])
                checks.append(_check("LLM 标注", ok, f"element={got.get('element')} "
                                                     f"trigger={got.get('trigger')}"))
                checks.append(_check("证据校验", ev_ok,
                                     f"{len(got.get('evidence') or [])} 条引用"))
                checks.append(_check("不产数值",
                                     got.get("gives_sp") is None
                                     and got.get("gives_ult") is None,
                                     "gives_sp/gives_ult 为空"))
                checks.append(_check("耗时", True,
                                     f"{int((time.time() - t0) * 1000)}ms "
                                     f"/ tokens {usage.get('prompt_tokens', 0)}"
                                     f"+{usage.get('completion_tokens', 0)}"))

    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "checks": checks,
            "available": llm_config.available(cfg),
            "model": cfg.get("model"),
            "prompt_version": llm.PROMPT_VERSION}
