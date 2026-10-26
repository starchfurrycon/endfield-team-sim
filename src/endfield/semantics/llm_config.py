"""LLM 配置：用户可以填自己的 OpenAI 兼容 API。

优先级：环境变量 > `data/llm.json` > 默认值。
密钥只存在本机（`data/` 已在 .gitignore 里），返回给前端时永远是掩码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import config

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

_ENV = {
    "base": ("ENDFIELD_LLM_BASE", "OPENAI_BASE_URL"),
    "key": ("ENDFIELD_LLM_API_KEY", "OPENAI_API_KEY"),
    "model": ("ENDFIELD_LLM_MODEL", "OPENAI_MODEL"),
    "enabled": ("ENDFIELD_LLM_ENABLED",),
}

DEFAULTS = {
    "enabled": False,
    "base": DEFAULT_BASE,
    "model": DEFAULT_MODEL,
    "key": "",
    "temperature": 0,
    "max_tokens": 2048,
    "timeout_s": 90,
    # 可选：用于估算成本（元/百万 token）；0 = 不估算
    "price_in": 0.0,
    "price_out": 0.0,
}


def path() -> Path:
    p = os.environ.get("ENDFIELD_LLM_CONFIG")
    return Path(p) if p else (config.DATA / "llm.json")


def _from_file() -> dict:
    try:
        # utf-8-sig：兼容 Windows 记事本保存的 BOM
        raw = json.loads(path().read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _env(name: str) -> str | None:
    for key in _ENV.get(name, ()):
        val = os.environ.get(key)
        if val:
            return val
    return None


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in _from_file().items() if k in DEFAULTS})
    for name in ("base", "key", "model"):
        val = _env(name)
        if val:
            cfg[name] = val
    env_enabled = _env("enabled")
    if env_enabled is not None:
        cfg["enabled"] = env_enabled.strip().lower() not in ("0", "false", "no", "off")
    elif cfg.get("key") and not _from_file().get("enabled"):
        # 只给了环境变量密钥：视为"显式启用"
        cfg["enabled"] = bool(_env("key"))
    cfg["base"] = str(cfg.get("base") or "").rstrip("/")
    cfg["model"] = str(cfg.get("model") or "").strip()
    cfg["key"] = str(cfg.get("key") or "").strip()
    return cfg


def validate(cfg: dict) -> list[str]:
    errs: list[str] = []
    base = str(cfg.get("base") or "")
    if not base.startswith(("http://", "https://")):
        errs.append("base 必须以 http:// 或 https:// 开头")
    if not str(cfg.get("model") or "").strip():
        errs.append("model 不能为空")
    try:
        t = float(cfg.get("temperature", 0))
        if not (0 <= t <= 2):
            errs.append("temperature 必须在 0~2")
    except (TypeError, ValueError):
        errs.append("temperature 必须是数字")
    try:
        mt = int(cfg.get("max_tokens", 2048))
        if not (128 <= mt <= 16384):
            errs.append("max_tokens 必须在 128~16384")
    except (TypeError, ValueError):
        errs.append("max_tokens 必须是整数")
    try:
        to = float(cfg.get("timeout_s", 90))
        if not (5 <= to <= 300):
            errs.append("timeout_s 必须在 5~300")
    except (TypeError, ValueError):
        errs.append("timeout_s 必须是数字")
    return errs


def save(patch: dict) -> dict:
    """合并保存。`key` 传空字符串表示"保持原值"，传 null 表示清空。"""
    cur = load()
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "key":
            if v is None:
                cur["key"] = ""
            elif str(v).strip():
                cur["key"] = str(v).strip()
            continue
        cur[k] = v
    errs = validate(cur)
    if errs:
        raise ValueError("；".join(errs))
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def mask_key(key: str) -> str:
    key = key or ""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}{'•' * 6}{key[-4:]}"


def public(cfg: dict | None = None) -> dict:
    """给前端的安全视图（不含明文密钥）。"""
    cfg = cfg or load()
    return {
        "enabled": bool(cfg.get("enabled")),
        "base": cfg.get("base") or "",
        "model": cfg.get("model") or "",
        "has_key": bool(cfg.get("key")),
        "key_masked": mask_key(cfg.get("key") or ""),
        "temperature": cfg.get("temperature", 0),
        "max_tokens": cfg.get("max_tokens", 2048),
        "timeout_s": cfg.get("timeout_s", 90),
        "price_in": cfg.get("price_in", 0.0),
        "price_out": cfg.get("price_out", 0.0),
        "config_path": str(path()),
        "from_env": bool(_env("base") or _env("key") or _env("model")),
    }


def available(cfg: dict | None = None) -> bool:
    cfg = cfg or load()
    return bool(cfg.get("enabled") and cfg.get("key") and cfg.get("base"))
