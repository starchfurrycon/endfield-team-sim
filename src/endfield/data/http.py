"""带缓存的 HTTP 抓取。

原则：
  * 内容寻址：文件已存在即命中缓存，force=True 才重下。
  * 失败不覆盖：抓取失败绝不写空文件。
  * 统一 UA、超时、有限重试。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config


class FetchError(RuntimeError):
    pass


class NotFound(FetchError):
    pass


_INDEX_NAME = "_http_index.json"


def _load_index(root: Path) -> dict:
    p = root / _INDEX_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_index(root: Path, idx: dict) -> None:
    (root / _INDEX_NAME).write_text(
        json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def _get_once(url: str, timeout: float, headers: dict | None = None) -> bytes:
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(f"404 {url}") from e
        raise


def fetch_any(urls: list[str], dest: Path, *, root: Path | None = None,
              force: bool = False, timeout: float = 30.0,
              retries: int = 1, sleep: float = 0.5,
              headers: dict | None = None) -> bytes:
    """依次尝试多个镜像 URL，任一成功即落盘。

    国内网络对 raw.githubusercontent.com 经常不可达，因此主用 jsDelivr CDN，
    并以 raw.githubusercontent 兜底。缓存命中即不联网。
    """
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest.read_bytes()

    last_err: Exception | None = None
    for url in urls:
        for attempt in range(retries):
            try:
                body = _get_once(url, timeout, headers)
                if not body:
                    raise FetchError("empty body")
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(body)
                tmp.replace(dest)
                if root is not None:
                    idx = _load_index(root)
                    idx[str(dest.relative_to(root)).replace("\\", "/")] = {
                        "url": url,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "bytes": len(body),
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    _save_index(root, idx)
                return body
            except NotFound:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < retries - 1:
                    time.sleep(sleep)
    raise FetchError(f"failed all mirrors for {dest.name}: {last_err}")


def fetch(url: str, dest: Path, *, root: Path | None = None,
          force: bool = False, timeout: float = 60.0,
          retries: int = 2, sleep: float = 1.0) -> bytes:
    return fetch_any([url], dest, root=root, force=force, timeout=timeout,
                     retries=retries, sleep=sleep)

