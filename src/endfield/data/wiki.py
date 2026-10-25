"""BWIKI（wiki.biligame.com/zmd）访问层。

只读三类东西：
  1. Semantic MediaWiki 查询 → 干员索引（中文名/稀有度/职业/属性/武器/TAG）
  2. MediaWiki:Skilldata.json   → 技能倍率交叉校验
  3. 页面 wikitext              → 技能中文描述、天赋、潜能、等级成长
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from . import http

_CACHE = config.RAW / "wiki"

# BWIKI 的 WAF 对默认 UA 返回 HTTP 567，必须伪装成浏览器访问。
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://wiki.biligame.com/zmd/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _ensure() -> None:
    _CACHE.mkdir(parents=True, exist_ok=True)


def _api(params: dict, *, dest: str | None = None, force: bool = False) -> object:
    _ensure()
    qs = urllib.parse.urlencode(params, safe="|")
    url = f"{config.WIKI_API}?{qs}"
    name = dest or ("_api_" + hashlib.sha1(qs.encode()).hexdigest()[:16] + ".json")
    body = http.fetch_any([url], _CACHE / name, root=_CACHE, force=force,
                          timeout=40.0, retries=2, headers=_BROWSER_HEADERS)
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------- SMW 索引

def operator_index(force: bool = False) -> list[dict]:
    """`[[分类:干员]]` 的结构化字段。"""
    query = ("[[分类:干员]]|?干员代号|?稀有度|?职业|?属性|?武器"
             "|?主属性|?副属性|?阵营|?TAG|limit=500")
    raw = _api({"action": "ask", "format": "json", "query": query},
               dest="smw_operators.json", force=force)
    results = raw.get("query", {}).get("results", {}) or {}
    out = []
    for title, row in results.items():
        po = row.get("printouts", {}) or {}

        def one(k):
            v = po.get(k)
            if isinstance(v, list) and v:
                return v[0]
            return None

        def many(k):
            v = po.get(k) or []
            return [x for x in v if isinstance(x, (str, int))]

        out.append({
            "title": row.get("fulltext") or title,
            "name": one("干员代号") or title,
            "rarity": one("稀有度"),
            "profession": one("职业"),
            "element": one("属性"),
            "weapon_type": one("武器"),
            "main_attr": one("主属性"),
            "sub_attr": one("副属性"),
            "faction": one("阵营"),
            "tags": many("TAG"),
            "url": row.get("fullurl"),
        })
    out.sort(key=lambda x: x["name"])
    return out


# ---------------------------------------------------------------- 倍率交叉校验

def skilldata(force: bool = False) -> dict:
    raw = _api({"action": "query", "prop": "revisions", "rvprop": "content",
                "rvslots": "main", "titles": "MediaWiki:Skilldata.json",
                "format": "json", "formatversion": "2"},
               dest="skilldata_raw.json", force=force)
    pages = raw.get("query", {}).get("pages", [])
    if not pages:
        return {}
    txt = pages[0]["revisions"][0]["slots"]["main"]["content"]
    d = json.loads(txt)
    (_CACHE / "skilldata.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


# ---------------------------------------------------------------- 页面文本

def parse_page(title: str, force: bool = False) -> dict | None:
    """返回 action=parse 的 parse 节点（含 wikitext 与 revid），并缓存。"""
    safe = urllib.parse.quote(title.replace(" ", "_"), safe="")
    dest = f"page_{safe[:80]}.json"
    try:
        raw = _api({"action": "parse", "page": title, "prop": "wikitext",
                    "format": "json", "formatversion": "2"},
                   dest=dest, force=force)
    except (http.NotFound, http.FetchError):
        return None
    if not isinstance(raw, dict) or "error" in raw:
        return None
    return raw.get("parse")


def page_wikitext(title: str, force: bool = False) -> str | None:
    p = parse_page(title, force=force)
    return (p or {}).get("wikitext")


def page_revid(title: str, force: bool = False) -> int | None:
    p = parse_page(title, force=force)
    return (p or {}).get("revid")


def site_meta(force: bool = False) -> dict:
    try:
        raw = _api({"action": "query", "meta": "siteinfo", "siprop": "general|statistics",
                    "format": "json"}, dest="siteinfo.json", force=force)
        q = raw.get("query", {})
        return {
            "generator": q.get("general", {}).get("generator"),
            "sitename": q.get("general", {}).get("sitename"),
            "articles": q.get("statistics", {}).get("articles"),
            "pages": q.get("statistics", {}).get("pages"),
        }
    except Exception:  # noqa: BLE001
        return {}


def polite_sleep() -> None:
    time.sleep(0.4)
