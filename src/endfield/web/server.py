"""本地 API + 静态页（stdlib，无依赖）。

给桌面壳（Electron）或浏览器用：
    python run.py serve --port 8777

首启（发行包里不带游戏数据）会在**后台线程**构建数据集，
页面通过 `/api/status` 轮询进度，构建完成后自动进入正常界面。
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
from .. import __version__
from ..model import Dataset
from ..engine.battle import Scenario
from ..search.rotations import search_rotation
from ..search.teams import TeamQuery, recommend

WEB_DIR = config.ROOT / "desktop"
_CACHE: dict = {"ds": None, "ts": 0.0}

# 构建状态（/api/status 的唯一来源）。只有 _run_build 会写它。
BUILD: dict = {
    "state": "idle",        # idle | building | ready | error
    "percent": 0,
    "message": "",
    "error": None,
    "version": None,
    "started_at": None,
    "finished_at": None,
}
_BUILD_LOCK = threading.Lock()

# LLM 复核任务状态（/api/llm 里的 run 字段）
LLM_RUN: dict = {
    "state": "idle",        # idle | running | done | error
    "message": "",
    "error": None,
    "stats": None,
    "started_at": None,
    "finished_at": None,
}
_LLM_LOCK = threading.Lock()


# ---------------------------------------------------------------- 数据集

def _dataset_ok(path: Path) -> bool:
    """本地数据集存在且 schema 与代码一致。"""
    if not path.exists():
        return False
    try:
        idx = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return idx.get("schema_version") == config.SCHEMA_VERSION


def dataset() -> Dataset:
    now = time.time()
    if _CACHE["ds"] is None or now - _CACHE["ts"] > 30:
        _CACHE["ds"] = Dataset.load(config.NORM)
        _CACHE["ts"] = now
    return _CACHE["ds"]


def status() -> dict:
    out = dict(BUILD)
    out["ready"] = BUILD["state"] == "ready"
    if BUILD.get("started_at"):
        end = BUILD.get("finished_at") or time.time()
        out["elapsed_s"] = round(end - BUILD["started_at"], 1)
    out["schema_version"] = config.SCHEMA_VERSION
    return out


def _run_build(verbose: bool = False) -> None:
    """构建数据集（同步）。进度写进 BUILD，失败不抛给调用方。"""
    with _BUILD_LOCK:
        BUILD.update(state="building", percent=0, message="准备构建数据…",
                     error=None, started_at=time.time(), finished_at=None)
    _CACHE["ds"] = None

    def report(frac: float, msg: str) -> None:
        # build 的 0~1 映射到整体 0~90%（后面还有语义层与报告）
        BUILD.update(percent=int(max(0.0, min(1.0, frac)) * 90), message=msg)

    try:
        if verbose:
            print("[serve] 首次运行：正在从数据源构建数据集（需要联网，约 1~3 分钟）…")
        from ..data import build as build_mod
        build_mod.build(verbose=verbose, progress=report)
        BUILD.update(percent=92, message="抽取技能语义…")
        try:
            from ..semantics import apply as sem
            from ..semantics import llm_config
            cfg = llm_config.load()
            ops = sem.load_operators()
            stats = sem.apply_to_operators(
                ops, use_llm=llm_config.available(cfg), cfg=cfg, verbose=verbose)
            sem.save_operators(ops)
            sem.write_semantics_report(ops, stats)
            if stats.get("llm_enabled"):
                sem.write_llm_review(ops, stats, cfg)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[serve] 语义层跳过：{type(e).__name__}")
        BUILD.update(percent=97, message="生成机制与常量报告…")
        try:
            from ..data import mechanics
            mechanics.write_report()
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[serve] mechanics 报告跳过：{type(e).__name__}")
        ds = Dataset.load(config.NORM)          # 校验真的能加载
        _CACHE.update(ds=ds, ts=time.time())
        BUILD.update(state="ready", percent=100, message="数据就绪",
                     version=ds.version, finished_at=time.time())
        if verbose:
            print(f"[serve] 数据就绪：{len(ds.operators)} 名干员，version={ds.version}")
    except Exception as e:  # noqa: BLE001
        BUILD.update(state="error", error=f"{type(e).__name__}: {e}",
                     message="构建失败", finished_at=time.time())
        if verbose:
            print(f"[serve] 构建失败：{type(e).__name__}: {e}")


def ensure_dataset(verbose: bool = True) -> bool:
    """确保数据集可用（阻塞）。保留给 CLI / 测试调用。"""
    if _dataset_ok(config.NORM / "dataset.json"):
        BUILD.update(state="ready", percent=100, message="数据就绪")
        return True
    _run_build(verbose=verbose)
    return BUILD["state"] == "ready"


def start_build(verbose: bool = False) -> None:
    """后台构建（首启不阻塞 HTTP 服务，页面才能看到进度）。"""
    if BUILD["state"] == "building":
        return
    threading.Thread(target=_run_build, kwargs={"verbose": verbose},
                     daemon=True, name="endfield-build").start()


# ---------------------------------------------------------------- 业务

# ---------------------------------------------------------------- LLM

def _llm_mod():
    from ..semantics import llm, llm_config
    return llm, llm_config


def llm_status() -> dict:
    llm, llm_config = _llm_mod()
    cfg = llm_config.load()
    pub = llm_config.public(cfg)
    st = llm.cache_stats()
    return {**pub,
            "available": llm_config.available(cfg),
            "prompt_version": llm.PROMPT_VERSION,
            "cache_files": st["files"],
            "cache_kb": round(st["bytes"] / 1024),
            "run": dict(LLM_RUN)}


def llm_save(payload: dict) -> dict:
    _, llm_config = _llm_mod()
    patch = {}
    for k in ("enabled", "base", "model", "key", "temperature", "max_tokens",
              "timeout_s", "price_in", "price_out"):
        if k in payload:
            patch[k] = payload[k]
    cfg = llm_config.save(patch)          # 校验失败抛 ValueError
    return {**llm_status(), "saved": True, "key_masked": llm_config.mask_key(cfg["key"])}


def llm_test() -> dict:
    llm, _ = _llm_mod()
    return llm.test_connection()


def _public_stats(stats: dict) -> dict:
    keep = ("skills", "known_trigger", "unknown_trigger", "unknown_rate",
            "hit_dependent", "llm_used", "llm_rejected", "llm_cache",
            "llm_calls", "repaired", "prompt_tokens", "completion_tokens",
            "cost", "mode", "prompt_version")
    out = {k: stats.get(k) for k in keep}
    out["changed_skills"] = len(stats.get("changes") or [])
    out["conflicts"] = len(stats.get("conflicts") or [])
    out["changes"] = (stats.get("changes") or [])[:40]
    out["conflict_items"] = (stats.get("conflicts") or [])[:20]
    return out


def _llm_job(mode: str = "gaps", limit: int | None = None) -> None:
    with _LLM_LOCK:
        LLM_RUN.update(state="running", message="加载数据集…", error=None,
                       stats=None, started_at=time.time(), finished_at=None)
    try:
        from ..semantics import apply as sem_apply
        llm, llm_config = _llm_mod()
        cfg = llm_config.load()
        if not llm_config.available(cfg):
            raise RuntimeError("LLM 未配置（需要启用 + API key）")
        ops = sem_apply.load_operators()
        if not ops:
            raise RuntimeError("没有数据集，先构建")
        LLM_RUN["message"] = f"复核 {len(ops)} 名干员的技能…"
        stats = sem_apply.apply_to_operators(
            ops, use_llm=True, mode=mode, limit=limit, cfg=cfg, verbose=False)
        sem_apply.save_operators(ops)
        sem_apply.write_semantics_report(ops, stats)
        sem_apply.write_llm_review(ops, stats, cfg)
        _CACHE["ds"] = None
        LLM_RUN.update(state="done", message="完成", stats=_public_stats(stats),
                       finished_at=time.time())
    except Exception as e:  # noqa: BLE001
        LLM_RUN.update(state="error", message="失败",
                       error=f"{type(e).__name__}: {e}", finished_at=time.time())


def start_llm_run(mode: str = "gaps", limit: int | None = None) -> dict:
    if LLM_RUN["state"] == "running":
        return dict(LLM_RUN)
    threading.Thread(target=_llm_job, kwargs={"mode": mode, "limit": limit},
                     daemon=True, name="endfield-llm").start()
    return dict(LLM_RUN)


def meta() -> dict:
    ds = dataset()
    from ..data import curated as curated_mod
    # 同一中文名可能有多份客户端数据（管理员 男/女/占位），UI 只该看到一个人，
    # 取技能数据最全的那份当代表，其余列进 variants 供"知道有分版"。
    groups: dict[str, list] = {}
    for op in ds.operators.values():
        if not op.name_zh:
            continue
        if not any(s.slot == "battle_skill" for s in op.skills.values()):
            continue
        groups.setdefault(op.name_zh, []).append(op)

    def rank(o) -> tuple:
        return (len(o.skills), o.rarity or 0, o.char_id)

    picked = [(name, max(ops, key=rank)) for name, ops in groups.items()]
    picked.sort(key=lambda x: (x[1].rarity or 0, x[0]), reverse=True)

    ops = []
    for name, op in picked:
        variants = sorted(o.char_id for o in groups[name])
        ops.append({
            "id": op.char_id,
            "name": op.name_zh,
            "eng_name": op.eng_name,
            "rarity": op.rarity,
            "profession": op.profession,
            "element": op.element,
            "weapon": op.weapon_type,
            "tags": op.battle_tags,
            "main_attr": op.main_attr,
            "combo_trigger": (op.semantics or {}).get("combo_trigger"),
            "hit_dependent": (op.semantics or {}).get("has_hit_dependent_skill"),
            "avatar": (config.AVATARS / f"{op.char_id}.png").is_file(),
            "variants": variants,
            "note": (curated_mod.spec_for(op.char_id) or {}).get("note"),
        })
    llm_info = {}
    try:
        llm, llm_config = _llm_mod()
        cfg = llm_config.load()
        llm_info = {"available": llm_config.available(cfg),
                    "model": cfg.get("model"), "prompt_version": llm.PROMPT_VERSION,
                    "cache_files": llm.cache_stats()["files"]}
    except Exception:  # noqa: BLE001
        pass
    return {"version": ds.version, "count": len(ops), "operators": ops,
            "app_version": __version__,
            "llm": llm_info,
            "constants": {k: v for k, v in config.GUESSED.items()},
            "enemies": {"slow": 12, "normal": 8, "fast": 5, "burst": 4},
            "equip_presets": [{"id": "none", "label": "不穿装备"},
                              {"id": "output", "label": "输出向套装"},
                              {"id": "sustain", "label": "功能向套装"}]}


def _apply_loadout(ds: Dataset, sc: Scenario, spec: dict) -> list[str]:
    """把「装备/武器预设」落到 Scenario 上，返回人类可读的说明。"""
    from ..search import loadout as lo
    return lo.apply_presets(ds, sc,
                            equip=spec.get("equip") or "none",
                            weapon=int(spec.get("weapon") or 0),
                            equip_level=int(spec.get("equip_level", 3)))


def _scenario(ds: Dataset, ids: list[str], payload: dict) -> Scenario:
    sc = Scenario(
        team=ids[:4],
        start_energy=str(payload.get("mode") or "cold"),
        duration_s=float(payload.get("duration", 40)),
        level=int(payload.get("level", 90)),
        skill_level=int(payload.get("skill_level", 12)),
        enemy_profile=str(payload.get("enemy_profile")
                          or payload.get("enemy") or "normal"),
    )
    _apply_loadout(ds, sc, payload)
    return sc


def axis(payload: dict) -> dict:
    ds = dataset()
    ids = payload.get("team") or []
    if not ids:
        raise ValueError("team 为空")
    sc = _scenario(ds, ids, payload)
    rr = search_rotation(ds, sc, beam=int(payload.get("beam", 3)),
                         steps=int(payload.get("steps", 26)),
                         opening_ults=(sc.start_energy == "hot"))
    return {"mode": sc.start_energy, "rotation": rr.to_dict(),
            "config": _config_summary(ds, sc)}


def _config_summary(ds: Dataset, sc: Scenario) -> dict:
    """给人看的场景摘要（UI 上显示"这一条轴是什么配置"）。"""
    names = {cid: (ds.operators[cid].name_zh or cid) for cid in sc.team
             if cid in ds.operators}
    equip = {}
    for cid, ids in (sc.equip_by_op or {}).items():
        equip[names.get(cid, cid)] = list(ids)
    weapons = {}
    for cid, wid in (sc.weapon_by_op or {}).items():
        weapons[names.get(cid, cid)] = wid
    return {
        "mode": sc.start_energy,
        "mode_label": "热启动" if sc.start_energy == "hot" else "冷启动",
        "enemy_profile": sc.enemy_profile,
        "duration_s": sc.duration_s,
        "level": sc.level,
        "skill_level": sc.skill_level,
        "equip_level": sc.equip_level,
        "equip_by_op": equip,
        "weapon_by_op": weapons,
        "notes": [f"{'热' if sc.start_energy == 'hot' else '冷'}启动",
                  f"敌人节奏 {sc.enemy_profile}",
                  f"{sc.duration_s:g}s 窗口"] + (
                      [f"装备 {len(equip)} 人" ] if equip else ["无装备"]) + (
                      [f"换武器 {len(weapons)} 人"] if weapons else []),
    }


def compare(payload: dict) -> dict:
    """两条方案并排对比（同队、不同启动方式/敌人节奏/装备/武器）。"""
    ds = dataset()
    ids = [x for x in (payload.get("team") or []) if x]
    if not ids:
        raise ValueError("team 为空")
    base = {k: payload[k] for k in ("duration", "level", "skill_level") if k in payload}
    beam = int(payload.get("beam", 3))
    steps = int(payload.get("steps", 26))
    names = {cid: (ds.operators[cid].name_zh or cid) for cid in ids[:4]
             if cid in ds.operators}

    out: dict = {"team": ids[:4], "name_map": names, "sides": {}}
    for side in ("a", "b"):
        spec = dict(base)
        spec.update(payload.get(side) or {})
        sc = _scenario(ds, ids, spec)
        rr = search_rotation(ds, sc, beam=beam, steps=steps,
                             opening_ults=(sc.start_energy == "hot"))
        cfg = _config_summary(ds, sc)
        out["sides"][side] = {
            "side": side.upper(),
            "label": " · ".join(cfg["notes"][:1] + cfg["notes"][2:]) or side.upper(),
            "config": cfg,
            "rotation": rr.to_dict(),
        }

    a = out["sides"]["a"]["rotation"]["result"]
    b = out["sides"]["b"]["rotation"]["result"]
    da, db = float(a["total_damage"]), float(b["total_damage"])
    base_dmg = max(1.0, da)
    out["delta"] = {
        "total_damage": round(db - da, 1),
        "dps": round(float(b["dps"]) - float(a["dps"]), 2),
        "damage_pct": round((db - da) / base_dmg * 100.0, 2),
        "duration_frames": int(b["duration_frames"]) - int(a["duration_frames"]),
    }
    return out


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
    res = recommend(ds, q, level=int(payload.get("level", 90)),
                    skill_level=int(payload.get("skill_level", 12)), verbose=False)
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
    server_version = "EndfieldSim/2.3"

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
        """数据没就绪时直接回 503，前端据此显示进度页。"""
        if BUILD["state"] == "ready":
            return True
        self._json(503, {"error": "数据集尚未就绪", "status": status()})
        return False

    # ---------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            f = WEB_DIR / "index.html"
            if not f.exists():
                return self._json(404, {"error": "desktop/index.html 不存在"})
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/avatars/"):
            name = path[len("/avatars/"):]
            if (not name.endswith(".png") or "/" in name or "\\" in name
                    or ".." in name):
                return self._json(404, {"error": "bad avatar name"})
            f = config.AVATARS / name
            if f.is_file():
                return self._send(200, f.read_bytes(), "image/png")
            return self._json(404, {"error": "no avatar"})
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
            try:
                return self._json(200, meta())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
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
            try:
                return self._json(200, axis(payload))
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        payload = self._body()
        try:
            if u.path == "/api/status":
                if payload.get("action") == "rebuild":
                    if BUILD["state"] != "building":
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
                return self._json(200, start_llm_run(
                    str(payload.get("mode") or "gaps"),
                    int(payload["limit"]) if payload.get("limit") else None))
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "not found"})


def serve(port: int = 8777, open_browser: bool = True,
          auto_build: bool = True) -> None:
    if auto_build:
        if _dataset_ok(config.NORM / "dataset.json"):
            BUILD.update(state="ready", percent=100, message="数据就绪")
            try:
                BUILD["version"] = dataset().version
            except Exception:  # noqa: BLE001
                pass
        else:
            start_build(verbose=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[serve] {url}")
    if BUILD["state"] != "ready":
        print("[serve] 数据集尚未就绪：页面会显示构建进度（完成后自动进入）")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
