"""数据源登记与版本锚点。

每个源都要能回答三个问题：
  1. 它是什么版本？（commit sha / revid）
  2. 它提供了哪些字段？（供 build 时写 source）
  3. 它的许可/使用限制是什么？
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from .. import config


def _api(url: str, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": config.USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def github_head(repo: str = config.GITHUB_REPO,
                branch: str = config.GITHUB_BRANCH) -> dict:
    """返回 {sha, date, repo, branch}。失败时抛异常，由调用方决定是否降级。"""
    info = _api(f"{config.GITHUB_API}/repos/{repo}/branches/{branch}")
    commit = info["commit"]
    return {
        "repo": repo,
        "branch": branch,
        "sha": commit["sha"],
        "short_sha": commit["sha"][:10],
        "date": commit["commit"]["committer"]["date"],
        "message": commit["commit"]["message"].splitlines()[0][:120],
    }


def wiki_siteinfo() -> dict:
    url = f"{config.WIKI_API}?action=query&meta=siteinfo&format=json&siprop=general"
    return _api(url)


def wiki_generator() -> str | None:
    try:
        info = wiki_siteinfo()
        return info.get("query", {}).get("general", {}).get("generator")
    except Exception:  # noqa: BLE001
        return None


SOURCE_META = {
    "github": {
        "name": "rmxlinux/EndfieldData",
        "url": f"https://github.com/{config.GITHUB_REPO}",
        "provides": [
            "干员基础属性与成长（CharacterTable）",
            "技能各等级倍率/消耗/冷却（SkillPatchTable）",
            "技能帧数据：durationFrame / exclusiveFrame / 命中帧（Json/SkillData）",
            "伤害公式常量（BattleConst）",
            "中文文本（I18nTextTable_CN）",
        ],
        "limits": "社区拆包备份，非官方；版本落后于游戏时字段可能缺失",
        "license": "数据版权归 Hypergryph；本仓库仅作研究用缓存",
    },
    "wiki": {
        "name": "BWIKI 明日方舟终末地",
        "url": config.WIKI_BASE,
        "provides": [
            "干员索引与标签（Semantic MediaWiki ask）",
            "技能中文描述/天赋/潜能（页面 wikitext）",
            "等级成长（/详细数据）",
            "技能倍率交叉校验（MediaWiki:Skilldata.json）",
        ],
        "limits": "站点自述仍在完善；部分新干员缺数据；请勿高频抓取",
        "license": "CC BY-NC-SA（BWIKI 惯例）；仅作语义来源",
    },
}


def stamp(path: Path, ref: str) -> dict:
    return {
        "ref": ref,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "path": str(path),
    }
