"""GitHub 数据仓（rmxlinux/EndfieldData）访问层。

只按需拉取：TableCfg 若干表 + 每个干员的技能文件（约 12 个/人）。
不 clone 整仓（65k 文件）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config
from . import http, sources


class GithubSource:
    def __init__(self, root: Path | None = None, repo: str = config.GITHUB_REPO,
                 branch: str = config.GITHUB_BRANCH):
        self.root = Path(root or (config.RAW / "github"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.repo = repo
        self.branch = branch
        self._head: dict | None = None

    # ------------------------------------------------------------ 版本

    def head(self, refresh: bool = False) -> dict:
        if self._head and not refresh:
            return self._head
        p = self.root / "_head.json"
        if p.exists() and not refresh:
            try:
                self._head = json.loads(p.read_text(encoding="utf-8"))
                return self._head
            except Exception:  # noqa: BLE001
                pass
        try:
            h = sources.github_head(self.repo, self.branch)
        except Exception as e:  # noqa: BLE001
            # 降级：用分支名当 ref，并在 head 里标记 unpinned
            h = {"repo": self.repo, "branch": self.branch, "sha": self.branch,
                 "short_sha": self.branch, "date": None,
                 "message": f"unpinned (api failed: {type(e).__name__})"}
        p.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
        self._head = h
        return h

    def raw_urls(self, relpath: str) -> list[str]:
        """回退链：jsDelivr（国内可达）→ gcore 镜像 → GitHub raw。"""
        ref = self.head()["sha"]
        return [
            f"https://cdn.jsdelivr.net/gh/{self.repo}@{ref}/{relpath}",
            f"https://gcore.jsdelivr.net/gh/{self.repo}@{ref}/{relpath}",
            f"{config.GITHUB_RAW}/{self.repo}/{ref}/{relpath}",
        ]

    def raw_url(self, relpath: str) -> str:
        return self.raw_urls(relpath)[0]

    # ------------------------------------------------------------ 取数

    def _get(self, relpath: str, force: bool = False) -> bytes:
        return http.fetch_any(self.raw_urls(relpath), self.root / relpath,
                              root=self.root, force=force)

    def table(self, name: str, force: bool = False) -> dict:
        body = self._get(f"TableCfg/{name}", force=force)
        return json.loads(body.decode("utf-8"))

    def table_optional(self, name: str) -> dict | None:
        try:
            return self.table(name)
        except Exception:  # noqa: BLE001
            return None

    def skill(self, skill_id: str, force: bool = False) -> dict | None:
        rel = f"Json/SkillData/{skill_id}.json"
        try:
            body = self._get(rel, force=force)
        except http.NotFound:
            return None
        except http.FetchError:
            return None
        return json.loads(body.decode("utf-8"))

    def buff(self, buff_id: str, force: bool = False) -> dict | None:
        rel = f"Json/BuffData/{buff_id}.json"
        try:
            body = self._get(rel, force=force)
        except http.NotFound:
            return None
        except http.FetchError:
            return None
        return json.loads(body.decode("utf-8"))

    # ------------------------------------------------------------ 便捷

    def local_path(self, relpath: str) -> Path:
        return self.root / relpath
