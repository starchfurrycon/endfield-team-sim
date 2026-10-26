"""归一化数据模型。

JSON 是唯一的交换格式；这里的 dataclass 只负责运行时类型与校验。
「未知」一律用 None / "unknown" 表达，绝不用 0 冒充。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------- 技能

@dataclass
class Hit:
    """一次伤害实例：帧位置 + 倍率取自哪个黑板 key。"""
    frame: int
    key: str | None = None
    damage_type: int | None = None   # 0=HP 1=失衡

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Timing:
    """帧级时间（30 fps）。"""
    duration_frames: int | None = None      # SkillData.durationFrame
    exclusive_frames: int | None = None     # SkillData.exclusiveFrame：承诺帧
    offset_frames: int | None = None        # SkillData.offsetRecordFrame
    hits: list[Hit] = field(default_factory=list)
    estimated: bool = False                 # 命中帧是兜底估计而非数据
    source: str = "SkillData"

    @property
    def hit_frames(self) -> list[int]:
        return [h.frame for h in self.hits if h.damage_type in (0, None)]

    @property
    def poise_frames(self) -> list[int]:
        return [h.frame for h in self.hits if h.damage_type == 1]

    @property
    def commit_frames(self) -> int:
        """最小承诺时间：没有 exclusive 数据时退化为 duration。"""
        if self.exclusive_frames is not None:
            return self.exclusive_frames
        if self.duration_frames is not None:
            return self.duration_frames
        return 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hits"] = [h.to_dict() for h in self.hits]
        d["hit_frames"] = self.hit_frames
        d["poise_frames"] = self.poise_frames
        return d


@dataclass
class SkillLevel:
    level: int
    multipliers: dict[str, float] = field(default_factory=dict)   # 黑板 key -> value
    sp_cost: float | None = None            # 技力消耗（100 = 1 段）
    cooldown_s: float | None = None
    ult_cost: float | None = None           # 仅终结技：所需终结技能量
    energy_gain: float | None = None        # 本技能提供的终结技能量（usp）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Skill:
    skill_id: str
    slot: str                                # normal_attack / battle_skill / ...
    stage: int = 0                           # 普攻第几段，1 起
    name_zh: str | None = None
    element: str | None = None               # 物理/灼热/电磁/寒冷/自然
    timing: Timing = field(default_factory=Timing)
    levels: list[SkillLevel] = field(default_factory=list)
    semantics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    role: str = "damage"          # damage / heal / buff / utility
    # wiki 的"展示口径"总倍率（12 级），用于给伤害总量定锚
    wiki_anchor: dict[str, Any] | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def level(self, lv: int = 12) -> SkillLevel | None:
        if not self.levels:
            return None
        lv = max(1, min(lv, len(self.levels)))
        return self.levels[lv - 1]

    # 倍率 key 的兜底顺序：不同干员的黑板 key 命名不统一，
    # 例如萤石/洛茜用 atk_scale_total / atk_scale_once，直接用 atk_scale 会取到 0。
    _MULT_FALLBACK_KEYS = ("atk_scale", "atk_scale_total", "atk_scale_once",
                           "atk_scale_base", "display_atk_scale")

    def _resolve_mult(self, mults: dict[str, float], key: str | None) -> float:
        if key and key in mults:
            return float(mults[key])
        for k in self._MULT_FALLBACK_KEYS:
            if k in mults and mults[k]:
                return float(mults[k])
        cands = [v for k, v in mults.items()
                 if k.startswith("atk_scale") and v]
        if cands:
            return float(max(cands))
        return 0.0

    def total_multiplier(self, lv: int = 1, *, prefer_wiki: bool = False) -> float:
        """一次施放的总倍率。

        默认按 DamageAction 逐帧求和（多段技能能正确累加）。
        `prefer_wiki=True` 时改用 wiki 的"效果1"展示值——注意那通常只是
        多个组成部分里的第一项，仅适合单段技能，因此默认关闭。
        """
        if prefer_wiki and self.wiki_anchor:
            arr = self.wiki_anchor.get("levels") or []
            if arr:
                i = max(0, min(lv - 1, len(arr) - 1))
                try:
                    return float(str(arr[i]).replace("%", "")) / 100.0
                except (TypeError, ValueError):
                    pass
        l = self.level(lv)
        if not l:
            return 0.0
        total = 0.0
        for h in self.timing.hits:
            if h.damage_type not in (0, None):
                continue
            total += self._resolve_mult(l.multipliers, h.key)
        if not self.timing.hits or total == 0.0:
            total = self._resolve_mult(l.multipliers, "atk_scale")
        return total

    def hp_hit_count(self) -> int:
        return len([h for h in self.timing.hits if h.damage_type in (0, None)])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timing"] = self.timing.to_dict()
        d["levels"] = [x.to_dict() for x in self.levels]
        return d


# ---------------------------------------------------------------- 干员

@dataclass
class Operator:
    char_id: str
    name_zh: str | None = None
    eng_name: str | None = None
    rarity: int | None = None
    profession: str | None = None
    element: str | None = None
    weapon_type: str | None = None
    main_attr: str | None = None
    sub_attr: str | None = None
    default_weapon_id: str | None = None
    weapon: dict = field(default_factory=dict)
    battle_tags: list[str] = field(default_factory=list)
    stats: list[dict] = field(default_factory=list)   # [{level, str, agi, wisd, will, max_hp, atk, def}]
    skills: dict[str, Skill] = field(default_factory=dict)
    normal_chain: list[str] = field(default_factory=list)  # 普攻链的 skill_id 顺序
    talents: list[dict] = field(default_factory=list)
    potentials: list[dict] = field(default_factory=list)
    wiki: dict[str, Any] = field(default_factory=dict)
    semantics: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def stat_at(self, level: int) -> dict:
        if not self.stats:
            return {}
        level = max(1, min(level, 90))
        for row in self.stats:
            if row.get("level") == level:
                return row
        return self.stats[min(level, len(self.stats)) - 1]

    def to_dict(self) -> dict:
        d = {
            "char_id": self.char_id,
            "name_zh": self.name_zh,
            "eng_name": self.eng_name,
            "rarity": self.rarity,
            "profession": self.profession,
            "element": self.element,
            "weapon_type": self.weapon_type,
            "main_attr": self.main_attr,
            "sub_attr": self.sub_attr,
            "default_weapon_id": self.default_weapon_id,
            "weapon": self.weapon,
            "battle_tags": self.battle_tags,
            "stats": self.stats,
            "normal_chain": self.normal_chain,
            "talents": self.talents,
            "potentials": self.potentials,
            "wiki": self.wiki,
            "semantics": self.semantics,
            "sources": self.sources,
            "skills": {k: v.to_dict() for k, v in self.skills.items()},
        }
        return d
    # ------------------------------------------------------------ io

    @classmethod
    def from_dict(cls, d: dict) -> "Operator":
        skills = {}
        for k, sd in (d.get("skills") or {}).items():
            t = sd.get("timing") or {}
            skills[k] = Skill(
                skill_id=sd["skill_id"],
                slot=sd.get("slot", k),
                stage=sd.get("stage", 0),
                name_zh=sd.get("name_zh"),
                element=sd.get("element"),
                timing=Timing(
                    duration_frames=t.get("duration_frames"),
                    exclusive_frames=t.get("exclusive_frames"),
                    offset_frames=t.get("offset_frames"),
                    hits=[Hit(**h) for h in (t.get("hits") or [])],
                    estimated=bool(t.get("estimated")),
                    source=t.get("source", "SkillData"),
                ),
                levels=[SkillLevel(**lv) for lv in (sd.get("levels") or [])],
                semantics=sd.get("semantics") or {},
                tags=sd.get("tags") or [],
                role=sd.get("role", "damage"),
                wiki_anchor=sd.get("wiki_anchor"),
                sources=sd.get("sources") or {},
            )
        return cls(
            char_id=d["char_id"],
            name_zh=d.get("name_zh"),
            eng_name=d.get("eng_name"),
            rarity=d.get("rarity"),
            profession=d.get("profession"),
            element=d.get("element"),
            weapon_type=d.get("weapon_type"),
            main_attr=d.get("main_attr"),
            sub_attr=d.get("sub_attr"),
            default_weapon_id=d.get("default_weapon_id"),
            weapon=d.get("weapon") or {},
            battle_tags=d.get("battle_tags") or [],
            stats=d.get("stats") or [],
            skills=skills,
            normal_chain=d.get("normal_chain") or [],
            talents=d.get("talents") or [],
            potentials=d.get("potentials") or [],
            wiki=d.get("wiki") or {},
            semantics=d.get("semantics") or {},
            sources=d.get("sources") or {},
        )

    @classmethod
    def load(cls, path: Path) -> "Operator":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------- 数据集

@dataclass
class Dataset:
    version: str
    created_at: str
    sources: dict[str, Any] = field(default_factory=dict)
    operators: dict[str, Operator] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def by_name(self) -> dict[str, Operator]:
        out = {}
        for op in self.operators.values():
            if op.name_zh:
                out[op.name_zh] = op
            if op.eng_name:
                out[op.eng_name.lower()] = op
            out[op.char_id] = op
        return out

    def resolve(self, key: str) -> Operator | None:
        by = self.by_name
        if key in by:
            return by[key]
        low = key.lower()
        for k, v in by.items():
            if k.lower() == low:
                return v
        for op in self.operators.values():
            if op.name_zh and key in op.name_zh:
                return op
        return None

    def resolve_all(self, key: str) -> list[Operator]:
        """同一个中文名可能对应多个 char_id（如「管理员」有男/女/默认三份）。"""
        out: list[Operator] = []
        low = key.lower()
        for op in self.operators.values():
            names = [op.char_id, (op.name_zh or ""), (op.eng_name or "")]
            if any(n and (n == key or n.lower() == low) for n in names):
                out.append(op)
        if out:
            return sorted(out, key=lambda o: o.char_id)
        for op in self.operators.values():
            if op.name_zh and key in op.name_zh:
                out.append(op)
        return out

    @classmethod
    def load(cls, root: Path) -> "Dataset":
        root = Path(root)
        idx = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
        ops = {}
        for cid in idx.get("operator_ids", []):
            p = root / "operators" / f"{cid}.json"
            if p.exists():
                ops[cid] = Operator.load(p)
        return cls(
            version=idx.get("version", "unknown"),
            created_at=idx.get("created_at", ""),
            sources=idx.get("sources", {}),
            operators=ops,
            coverage=idx.get("coverage", {}),
        )
