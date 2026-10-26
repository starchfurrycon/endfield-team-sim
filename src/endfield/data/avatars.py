"""干员头像（可选增强）。

客户端表里没有头像路径，所以从社区 wiki（Talos，wiki.gg）取 120px 图标，
缓存到 `data/avatars/<char_id>.png`。

流程：`File:<英文名> icon.png` → 失败则 wiki 搜索该名字拿规范页名再试
（例如客户端写 `Mifu`，wiki 页是 `Mi Fu`）。

设计约束（和文本数据一致）：
  * 仓库不附带图片，运行时抓取、只落本地缓存；
  * **失败不影响构建**：UI 退化成"元素色块 + 首字"的占位头像；
  * 连续失败熔断（wiki 不可达时别把首启拖成十几分钟）；
  * 只接受真正的 PNG（防止把错误页存成图片）。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config

WIKI_API = "https://endfield.wiki.gg/api.php"
WIKI_IMAGE = "https://endfield.wiki.gg/images/{name}_icon.png"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
UA = "endfield-team-sim/2.4 (research; local)"
MAP_NAME = "_names.json"


def dest_for(char_id: str) -> Path:
    return config.AVATARS / f"{char_id}.png"


def _map_path() -> Path:
    return config.AVATARS / MAP_NAME


def _load_map() -> dict:
    try:
        return json.loads(_map_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_map(m: dict) -> None:
    try:
        _map_path().write_text(json.dumps(m, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def _get(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _search_title(name: str) -> str | None:
    """wiki 搜索：拿规范页名（跳过 Portrait/Theme 之类的非干员页）。"""
    try:
        url = WIKI_API + "?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": name,
            "srlimit": 5, "format": "json"})
        data = json.loads(_get(url).decode("utf-8"))
        for row in (data.get("query") or {}).get("search") or []:
            title = row.get("title") or ""
            if "(" in title or ":" in title:
                continue
            return title
    except Exception:  # noqa: BLE001
        return None
    return None


def _valid_png(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(8) == PNG_MAGIC and path.stat().st_size > 200
    except OSError:
        return False


def _try_save(dest: Path, url: str) -> bool:
    try:
        body = _get(url)
    except Exception:  # noqa: BLE001
        return False
    if not body.startswith(PNG_MAGIC) or len(body) < 200:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return True


def fetch_one(eng_name: str, char_id: str, *, force: bool = False,
              timeout: float = 20.0) -> str:
    """返回 'ok' / 'cached' / 'missing' / 'fail'。"""
    dest = dest_for(char_id)
    if dest.exists() and not force:
        return "cached" if _valid_png(dest) else "fail"
    key = str(eng_name or "").strip()
    if not key:
        return "missing"
    cache = _load_map()
    title = cache.get(char_id) or key
    if _try_save(dest, WIKI_IMAGE.format(name=urllib.parse.quote(title.replace(" ", "_")))):
        cache[char_id] = title
        _save_map(cache)
        return "ok"
    found = _search_title(key)
    if found and found != title:
        if _try_save(dest, WIKI_IMAGE.format(
                name=urllib.parse.quote(found.replace(" ", "_")))):
            cache[char_id] = found
            _save_map(cache)
            return "ok"
    return "missing"


def fetch_many(operators: dict, *, force: bool = False,
               verbose: bool = False, max_failures: int = 3) -> dict:
    """抓一批头像。连续 max_failures 次失败即熔断（网络不可达时不拖慢首启）。"""
    config.ensure_dirs()
    stats = {"ok": 0, "cached": 0, "missing": 0, "fail": 0, "total": len(operators)}
    streak = 0
    skipped = False
    for op in operators.values():
        if skipped:
            stats["missing"] += 1
            continue
        res = fetch_one(op.eng_name or "", op.char_id, force=force)
        stats[res] = stats.get(res, 0) + 1
        if res == "fail":
            streak += 1
            if streak >= max_failures:
                skipped = True
                if verbose:
                    print("[avatars] wiki 不可达，跳过头像下载（UI 用占位图）")
        else:
            streak = 0
    if verbose:
        print(f"[avatars] 头像 {stats['ok']} 新下载 / {stats['cached']} 缓存 / "
              f"{stats['missing']} 缺失 / {stats['fail']} 失败（共 {stats['total']}）")
    return stats
