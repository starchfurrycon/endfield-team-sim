"""本地 API + 静态页（stdlib，无依赖）。

给桌面壳（Electron）或浏览器用：
    python run.py serve --port 8777
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config
from ..model import Dataset
from ..engine.battle import Result, Scenario
from ..search.rotations import search_rotation
from ..search.teams import TeamQuery, recommend

WEB_DIR = config.ROOT / "desktop"
_CACHE: dict = {"ds": None, "ts": 0.0}


def dataset() -> Dataset:
    now = time.time()
    if _CACHE["ds"] is None or now - _CACHE["ts"] > 30:
        _CACHE["ds"] = Dataset.load(config.NORM)
        _CACHE["ts"] = now
    return _CACHE["ds"]


def meta() -> dict:
    ds = dataset()
    ops = []
    for op in sorted(ds.operators.values(), key=lambda o: (o.rarity or 0, o.char_id),
                     reverse=True):
        if not any(s.slot == "battle_skill" for s in op.skills.values()):
            continue
        ops.append({
            "id": op.char_id,
            "name": op.name_zh,
            "rarity": op.rarity,
            "profession": op.profession,
            "element": op.element,
            "weapon": op.weapon_type,
            "tags": op.battle_tags,
            "main_attr": op.main_attr,
            "combo_trigger": (op.semantics or {}).get("combo_trigger"),
            "hit_dependent": (op.semantics or {}).get("has_hit_dependent_skill"),
        })
    return {"version": ds.version, "count": len(ops), "operators": ops,
            "constants": {k: v for k, v in config.GUESSED.items()}}


def axis(payload: dict) -> dict:
    ds = dataset()
    ids = payload.get("team") or []
    if len(ids) < 1:
        raise ValueError("team 为空")
    mode = payload.get("mode", "cold")
    sc = Scenario(team=ids[:4], start_energy=mode,
                  duration_s=float(payload.get("duration", 40)),
                  level=int(payload.get("level", 80)),
                  skill_level=int(payload.get("skill_level", 12)),
                  enemy_profile=str(payload.get("enemy_profile") or "normal"))
    rr = search_rotation(ds, sc, beam=int(payload.get("beam", 3)),
                         steps=int(payload.get("steps", 26)),
                         opening_ults=(mode == "hot"))
    return {"mode": mode, "rotation": rr.to_dict()}


def team(payload: dict) -> dict:
    ds = dataset()
    q = TeamQuery(
        must=payload.get("must") or [],
        ban=payload.get("ban") or [],
        pool=payload.get("pool") or [],
        size=int(payload.get("size", 4)),
        top_k=int(payload.get("top", 3)),
        evaluate=int(payload.get("evaluate", 8)),
        duration_s=float(payload.get("duration", 40)),
    )
    res = recommend(ds, q, level=int(payload.get("level", 80)),
                    skill_level=int(payload.get("skill_level", 7)), verbose=False)
    names = {op.char_id: op.name_zh for op in ds.operators.values()}
    for t in res["top"]:
        t["name_map"] = {cid: names.get(cid, cid) for cid in t["team"]}
    return res


def loadout(payload: dict) -> dict:
    from ..search import loadout as lo
    ds = dataset()
    ids = [x for x in (payload.get("team") or []) if x]
    if not ids:
        raise ValueError("team 为空")
    return {"team": ids,
            "recommendations": lo.recommend_for_team(
                ds, ids[:4],
                int(payload.get("weapons", 3)),
                int(payload.get("sets", 2)))}


class Handler(BaseHTTPRequestHandler):
    server_version = "EndfieldSim/0.1"

    def log_message(self, fmt, *args):  # 静音
        pass

    # ---------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
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

    # ---------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            f = WEB_DIR / "index.html"
            if not f.exists():
                return self._json(404, {"error": "desktop/index.html 不存在"})
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/meta":
            try:
                return self._json(200, meta())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/axis":
            q = urllib.parse.parse_qs(u.query)
            payload = {
                "team": [x for x in (q.get("team", [""])[0]).split(",") if x],
                "mode": q.get("mode", ["cold"])[0],
                "duration": q.get("duration", ["40"])[0],
                "enemy_profile": q.get("enemy", ["normal"])[0],
            }
            try:
                return self._json(200, axis(payload))
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        payload = self._body()
        try:
            if u.path == "/api/team":
                return self._json(200, team(payload))
            if u.path == "/api/loadout":
                return self._json(200, loadout(payload))
            if u.path == "/api/axis":
                return self._json(200, axis(payload))
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})


def ensure_dataset(verbose: bool = True) -> bool:
    """首次运行时自动构建数据集（发行包里不附带游戏原始数据）。"""
    ds_path = config.NORM / "dataset.json"
    if ds_path.exists():
        try:
            import json as _json
            meta = _json.loads(ds_path.read_text(encoding="utf-8"))
            if meta.get("schema_version") == config.SCHEMA_VERSION:
                return True
            if verbose:
                print(f"[serve] 数据 schema {meta.get('schema_version')} → "
                      f"{config.SCHEMA_VERSION}，重建数据集…")
        except Exception:  # noqa: BLE001
            pass
    if verbose:
        print("[serve] 首次运行：正在从数据源构建数据集（需要联网，约 1~3 分钟）…")
    from ..data import build as build_mod
    build_mod.build(verbose=verbose)
    try:
        from ..semantics import apply as sem
        ops = sem.load_operators()
        stats = sem.apply_to_operators(ops, use_llm=False, verbose=False)
        sem.save_operators(ops)
        sem.write_semantics_report(ops, stats)
    except Exception as e:  # noqa: BLE001
        print(f"[serve] 语义层跳过：{type(e).__name__}")
    try:
        from ..data import mechanics
        mechanics.write_report()
    except Exception as e:  # noqa: BLE001
        print(f"[serve] mechanics 报告跳过：{type(e).__name__}")
    return True


def serve(port: int = 8777, open_browser: bool = True,
          auto_build: bool = True) -> None:
    if auto_build:
        try:
            ensure_dataset()
        except Exception as e:  # noqa: BLE001
            print(f"[serve] 构建失败：{type(e).__name__}: {e}")
            print("[serve] 仍会启动 UI，但推荐功能不可用")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[serve] {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
