"""本地 HTTP 服务（stdlib，无依赖）。

职责只有三件：静态页 / 头像、API 路由、启动时把状态机对齐。
业务逻辑在 `web/api.py`，后台任务在 `web/jobs.py`，状态在 `web/state.py`。
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, config
from . import api, jobs
from .state import STATE


def status() -> dict:
    return api.status()


def meta() -> dict:
    return api.meta()


def axis(payload: dict) -> dict:
    return api.axis(payload)


def team(payload: dict) -> dict:
    return api.team(payload)


def compare(payload: dict) -> dict:
    return api.compare(payload)


def loadout(payload: dict) -> dict:
    return api.loadout(payload)


def llm_status() -> dict:
    return api.llm_status()


def llm_save(payload: dict) -> dict:
    return api.llm_save(payload)


def llm_test() -> dict:
    return api.llm_test()


def start_llm_run(mode: str = "gaps", limit: int | None = None) -> dict:
    return api.start_llm_run(mode=mode, limit=limit)


def start_build(verbose: bool = False) -> None:
    jobs.start_build(verbose=verbose)


class Handler(BaseHTTPRequestHandler):
    server_version = f"EndfieldSim/{__version__}"

    def log_message(self, fmt, *args):  # 静音
        pass

    # ---------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _need_data(self) -> bool:
        if STATE.data_ready():
            return True
        self._json(503, {"error": "数据集尚未就绪", "status": status()})
        return False

    def _serve_file(self, path: Path, ctype: str, code: int = 200) -> None:
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        return self._send(code, path.read_bytes(), ctype)

    # ---------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            return self._serve_file(config.ROOT / "desktop" / "index.html",
                                    "text/html; charset=utf-8")
        if path.startswith("/avatars/"):
            name = path[len("/avatars/"):]
            if (not name.endswith(".png") or "/" in name or "\\" in name
                    or ".." in name):
                return self._json(404, {"error": "bad avatar name"})
            return self._serve_file(config.AVATARS / name, "image/png")
        if path == "/api/status":
            return self._json(200, status())
        if path == "/api/llm":
            try:
                return self._json(200, llm_status())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/meta":
            if not self._need_data():
                return None
            return self._safe(meta)
        if path == "/api/axis":
            if not self._need_data():
                return None
            q = urllib.parse.parse_qs(u.query)
            payload = {
                "team": [x for x in (q.get("team", [""])[0]).split(",") if x],
                "mode": q.get("mode", ["cold"])[0],
                "duration": q.get("duration", ["40"])[0],
                "enemy_profile": q.get("enemy", ["normal"])[0],
                "equip": q.get("equip", ["none"])[0],
                "weapon": q.get("weapon", ["0"])[0],
                "equip_level": q.get("equip_level", ["3"])[0],
                "level": q.get("level", ["90"])[0],
                "skill_level": q.get("skill_level", ["12"])[0],
            }
            return self._safe(lambda: axis(payload))
        return self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        payload = self._body()
        try:
            if u.path == "/api/status":
                if payload.get("action") == "rebuild" and not STATE.data.state == "building":
                    start_build(verbose=True)
                return self._json(200, status())
            if u.path in ("/api/team", "/api/loadout", "/api/axis", "/api/compare"):
                if not self._need_data():
                    return None
            if u.path == "/api/team":
                return self._json(200, team(payload))
            if u.path == "/api/loadout":
                return self._json(200, loadout(payload))
            if u.path == "/api/axis":
                return self._json(200, axis(payload))
            if u.path == "/api/compare":
                return self._json(200, compare(payload))
            if u.path == "/api/llm":
                return self._json(200, llm_save(payload))
            if u.path == "/api/llm/test":
                return self._json(200, llm_test())
            if u.path == "/api/llm/run":
                if not self._need_data():
                    return None
                limit = int(payload["limit"]) if payload.get("limit") else None
                return self._json(200, start_llm_run(
                    str(payload.get("mode") or "gaps"), limit))
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})

    def _safe(self, fn) -> None:
        try:
            return self._json(200, fn())
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})


def serve(port: int = 8777, open_browser: bool = True,
          auto_build: bool = True) -> None:
    jobs.boot(auto_build=auto_build, verbose=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[serve] {url}")
    if not STATE.data_ready():
        print("[serve] 数据集尚未就绪：页面会显示构建进度（完成后自动进入）")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
