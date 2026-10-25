"""全局配置与常量。

设计原则（见 docs/02-计划.md §1）：
  * 任何"猜"出来的数字都必须在 GUESSED 里，带 source="estimate"，
    并在结果输出时被列进 warnings。
  * 任何来自数据表的数字，source 指向 表名/字段。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- 路径

# 冻结（PyInstaller）时：ROOT 指向解包出的只读资源目录，数据写到 exe 旁边，
# 这样用户重复运行不会每次重新下载。
_FROZEN = bool(getattr(sys, "frozen", False))
if _FROZEN:
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_HOME = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parents[2]
    APP_HOME = ROOT

DATA = APP_HOME / "data"
RAW = DATA / "raw"
NORM = DATA / "normalized"
REPORTS = DATA / "reports"
EXPORTS = APP_HOME / "exports"
LLM_CACHE = DATA / "llm"


def ensure_dirs() -> None:
    for p in (RAW, NORM, REPORTS, LLM_CACHE):
        p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 数据源

GITHUB_REPO = "rmxlinux/EndfieldData"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_BRANCH = "main"

WIKI_API = "https://wiki.biligame.com/zmd/api.php"
WIKI_BASE = "https://wiki.biligame.com/zmd/"

USER_AGENT = "endfield-team-sim/0.1 (research; contact: local)"

# 只拉这些表（其余按需）
TABLES = [
    "CharacterTable.json",
    "SkillPatchTable.json",
    "BattleConst.json",
    "AttributeMetaTable.json",
    "CharTypeTable.json",
    "CharProfessionTable.json",
    "CharBattleTagTable.json",
    "TextTable.json",
    "I18nTextTable_CN.json",
]

# ---------------------------------------------------------------- 时间

FPS = 30                      # 帧率：社区对 626 条动画的换算验证（见调研 §2.2）
FRAME_MS = 1000.0 / FPS


def frames_to_s(f: float) -> float:
    return round(f / FPS, 3)


def s_to_frames(s: float) -> int:
    return int(round(s * FPS))


# ---------------------------------------------------------------- 属性 id

# 来源：AttributeMetaTable.json / wiki「数值范围」页
ATTR_LEVEL = 0
ATTR_MAX_HP = 1
ATTR_ATK = 2
ATTR_DEF = 3
ATTR_STR = 39
ATTR_AGI = 40
ATTR_WISD = 41
ATTR_WILL = 42
ATTR_CRIT_RATE = 9
ATTR_CRIT_DMG = 10
ATTR_MAX_ULT_SP = 22
ATTR_MAX_POISE = 20
ATTR_POISE_REC_TIME = 21

ATTR_NAMES = {0: "level", 1: "max_hp", 2: "atk", 3: "def",
              39: "str", 40: "agi", 41: "wisd", 42: "will",
              9: "crit_rate", 10: "crit_dmg", 20: "max_poise",
              21: "poise_rec_time", 22: "max_ult_sp"}

# 职业 id -> 名称（CharProfessionTable 的 name 在 i18n 里，这里只做兜底）
PROFESSION_FALLBACK = {0: "近卫", 1: "重装", 2: "狙击", 3: "术师",
                       4: "医疗", 5: "辅助", 6: "特种", 7: "先锋", 8: "突击"}

# 元素 charTypeId -> 中文
ELEMENT_ZH = {"Physical": "物理", "Fire": "灼热", "Pulse": "电磁",
              "Cryst": "寒冷", "Natural": "自然", "Ether": "超域"}

# 技能槽位（对应 SkillData 文件名后缀）
SLOT_SUFFIX = {
    "battle_skill": "normal_skill",
    "combo_skill": "combo_skill",
    "ultimate": "ultimate_skill",
    "power_attack": "power_attack",
    "dash_attack": "dash_attack",
    "plunging_attack": "plunging_attack_end",
}

SLOT_ZH = {
    "normal_attack": "普攻",
    "power_attack": "重击",
    "battle_skill": "战技",
    "combo_skill": "连携技",
    "ultimate": "终结技",
    "dash_attack": "冲刺攻击",
    "plunging_attack": "下落攻击",
    "execute": "处决",
}

# ---------------------------------------------------------------- 猜测常量

# 这些值没有可直接读出的数据依据，属于工程假设。
# 它们的值集中在 config，便于用户校准，也便于在输出里如实标注。
GUESSED: dict[str, dict] = {
    "SWITCH_FRAMES": {"value": 12, "unit": "frame",
                      "source": "estimate",
                      "note": "切换主控的操作开销，无表数据，按 0.4s 估"},
    "AUTO_ATTACK_GAP_FRAMES": {"value": 8, "unit": "frame",
                               "source": "estimate; 机制见 wiki_tut_bat_team_1",
                               "note": "非主控干员自动普攻的段间间隔（机制有据，间隔无据）"},
    "SP_MAX": {"value": 300, "unit": "point", "source": "wiki",
               "note": "全队共享 3 段技力，1 段 = 100"},
    "SP_REGEN_PER_SEC": {"value": 8.0, "unit": "point/s",
                         "source": "estimate",
                         "note": "技力自然恢复速率：客户端表里只有修正项"
                                 "（buff_dung_atb_recoverspeed 的 triggerInterval=-1、"
                                 "ratio=-0.7 等），基础速率在引擎侧，仍需实测"},
    "SP_GAIN_HEAVY": {"value": 100, "unit": "point",
                      "source": "BuffData:buff_dung_atb_add_lastnormalattack",
                      "note": "重击命中回复技力：黑板 atb=1.0，按 100 点/段换算"},
    "SP_GAIN_EXECUTE": {"value": 100, "unit": "point", "source": "wiki 教学页",
                        "note": "处决回复一定技力（wiki_tut_bat_execute_1），数值未单列，暂按 1 段"},
    "SP_GAIN_PERFECT_DODGE": {"value": 100, "unit": "point",
                              "source": "BuffData:buff_dung_atb_add_perfectdodge",
                              "note": "极限闪避回复技力：黑板 atb=1.0"},
    "SP_GAIN_KILL": {"value": 50, "unit": "point",
                     "source": "BuffData:buff_dung_atb_add_kill",
                     "note": "击杀回复技力：黑板 atb=0.5"},
    "ULT_GAIN_BATTLE_SKILL": {"value": 6.5, "unit": "point",
                              "source": "BuffData:buff_common_obtain_ultimate_sp",
                              "note": "施放战技时全队获得的终结技能量"
                                      "（buff 黑板 usp_everyone=6.5，数据口径）"},
    "ULT_GAIN_COMBO": {"value": 6.5, "unit": "point", "source": "estimate",
                       "note": "连携技回能；多数技能自带 usp 黑板，优先用表值"},
    "ULT_GAIN_NORMAL_HIT": {"value": 0, "unit": "point", "source": "estimate",
                            "note": "普攻命中是否回能，存疑，默认不回"},
    "COMBO_WINDOW_FRAMES": {"value": 150, "unit": "frame", "source": "wiki",
                            "note": "连携技触发后约 5 秒窗口"},
    "ENEMY_ATTACK_INTERVAL": {"value": 240, "unit": "frame", "source": "scenario",
                              "note": "默认敌人出手间隔（场景可覆盖）"},
    "ENEMY_DEF": {"value": 100, "unit": "point", "source": "community",
                  "note": "多数敌人 DEF=100"},
    "ENEMY_POISE": {"value": 100, "unit": "point", "source": "estimate"},
    "CRIT_RATE_BASE": {"value": 0.05, "source": "community"},
    "CRIT_DMG_BASE": {"value": 0.5, "source": "community"},
    "STAGGER_DMG_MULT": {"value": 1.3, "source": "community",
                         "note": "失衡状态独立乘区"},
    "ATK_RATE_MAIN": {"value": 0.005, "source": "BattleConst.atkRateOfMain",
                      "note": "主属性对攻击力的加成系数"},
    "ATK_RATE_SUB": {"value": 0.002, "source": "BattleConst.atkRateOfSub",
                     "note": "副属性对攻击力的加成系数"},
    "SKILL_LEVEL_DEFAULT": {"value": 7, "source": "assumption",
                            "note": "默认技能等级（0 氪/常规培养的中位）"},
    "OPERATOR_LEVEL_DEFAULT": {"value": 80, "source": "assumption"},
}


def guess(name: str) -> float:
    return float(GUESSED[name]["value"])


# ---------------------------------------------------------------- 环境

def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)
