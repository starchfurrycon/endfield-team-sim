"""应用状态机：数据构建 + LLM 复核。

只负责**状态与转移**，不做 IO（构建/调用在 jobs.py）。
这样状态机可以单测，HTTP 层也不必自己维护两份 dict。

数据状态：idle ──start──> building ──ok──> ready
                              └────err──> error ──start──> building
LLM 状态：idle ──start（要求 data=ready）──> running ──ok──> done
                                                   └──err──> error
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .. import config
from ..model import Dataset

DATA_STATES = ("idle", "building", "ready", "error")
LLM_STATES = ("idle", "running", "done", "error")


@dataclass
class Stage:
    state: str = "idle"
    percent: int = 0
    message: str = ""
    error: str | None = None
    version: str | None = None
    stats: dict | None = None
    started_at: float | None = None
    finished_at: float | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self, ready_state: str = "ready") -> dict:
        out = {
            "state": self.state,
            "percent": self.percent,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ready": self.state == ready_state,
        }
        if self.version:
            out["version"] = self.version
        if self.stats is not None:
            out["stats"] = self.stats
        if self.started_at:
            end = self.finished_at or time.time()
            out["elapsed_s"] = round(max(0.0, end - self.started_at), 1)
        out.update(self.extra)
        return out


class AppState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.data = Stage()
        self.llm = Stage()
        self.events: deque = deque(maxlen=100)
        self._cache: dict = {"ds": None, "ts": 0.0}

    # ------------------------------------------------------------ 内部

    def _event(self, kind: str, **detail) -> None:
        self.events.append({"at": time.strftime("%H:%M:%S"), "kind": kind,
                            **{k: v for k, v in detail.items() if v is not None}})

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # ------------------------------------------------------------ 数据

    def begin_build(self) -> bool:
        with self._lock:
            if self.data.state == "building":
                return False
            self.data = Stage(state="building", percent=0,
                              message="准备构建数据…", started_at=time.time())
            self._cache["ds"] = None
            self._event("build.start")
            return True

    def build_progress(self, percent: int, message: str) -> None:
        with self._lock:
            if self.data.state != "building":
                return
            self.data.percent = max(0, min(100, int(percent)))
            self.data.message = message

    def build_done(self, version: str) -> None:
        with self._lock:
            self.data.state = "ready"
            self.data.percent = 100
            self.data.message = "数据就绪"
            self.data.version = version
            self.data.error = None
            self.data.finished_at = time.time()
            self._event("build.done", version=version)

    def build_failed(self, error: str) -> None:
        with self._lock:
            self.data.state = "error"
            self.data.message = "构建失败"
            self.data.error = error
            self.data.finished_at = time.time()
            self._event("build.error", error=error[:120])

    def data_ready(self) -> bool:
        return self.data.state == "ready"

    def mark_ready(self, version: str) -> None:
        """启动时发现本地已有可用数据集：直接对齐到 ready。"""
        with self._lock:
            self.data.state = "ready"
            self.data.percent = 100
            self.data.message = "数据就绪"
            self.data.version = version
            self.data.error = None
            self._event("build.reuse", version=version)

    # ------------------------------------------------------------ LLM

    def begin_llm(self) -> bool:
        """数据必须就绪；同一时间只允许一个复核任务。"""
        with self._lock:
            if self.data.state != "ready":
                raise RuntimeError("数据尚未就绪，不能运行 LLM 复核")
            if self.llm.state == "running":
                return False
            self.llm = Stage(state="running", message="加载数据集…",
                             started_at=time.time())
            self._event("llm.start")
            return True

    def llm_progress(self, message: str) -> None:
        with self._lock:
            if self.llm.state == "running":
                self.llm.message = message

    def llm_done(self, stats: dict) -> None:
        with self._lock:
            self.llm.state = "done"
            self.llm.message = "完成"
            self.llm.stats = stats
            self.llm.finished_at = time.time()
            self._cache["ds"] = None          # 语义变了，重新加载
            self._event("llm.done")

    def llm_failed(self, error: str) -> None:
        with self._lock:
            self.llm.state = "error"
            self.llm.message = "失败"
            self.llm.error = error
            self.llm.finished_at = time.time()
            self._event("llm.error", error=error[:120])

    # ------------------------------------------------------------ 数据集

    def dataset(self) -> Dataset:
        with self._lock:
            now = time.time()
            if self._cache["ds"] is None or now - self._cache["ts"] > 30:
                self._cache["ds"] = Dataset.load(config.NORM)
                self._cache["ts"] = now
            return self._cache["ds"]

    def invalidate(self) -> None:
        with self._lock:
            self._cache.update(ds=None, ts=0.0)

    # ------------------------------------------------------------ 快照

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "data": self.data.as_dict(),
                "llm": self.llm.as_dict(ready_state="done"),
                "schema_version": config.SCHEMA_VERSION,
                "events": list(self.events)[-20:],
            }


# 全局单例（HTTP 层与任务层共用）
STATE = AppState()


def dataset_ok(path=None) -> bool:
    """本地数据集存在且 schema 与代码一致。"""
    import json
    from pathlib import Path

    p = Path(path or (config.NORM / "dataset.json"))
    if not p.exists():
        return False
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return idx.get("schema_version") == config.SCHEMA_VERSION
