# -*- coding: utf-8 -*-
"""文字修仙 v2：属性 / 场景 / 炼丹 / 炼器 / 历练 / 渡劫 / 离线收益。

灵感参考了几个开源文字修仙项目（云隐修仙录、vue-XiuXianGame、idle-cultivation-game）：
放置收益、灵根资质、悟性气运、丹药法器、场景化行动、渡劫风险。
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "rpg.db"
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

REALMS = ["练气", "筑基", "金丹", "元婴", "化神", "炼虚", "合体", "大乘", "渡劫"]
SUB = ["初期", "中期", "后期"]
ROOTS = {
    "metal": ("金灵根", 1.15, "攻伐果断，历练更稳"),
    "wood": ("木灵根", 1.0, "生机绵长，采药更多"),
    "water": ("水灵根", 1.05, "悟性通透，闭关更快"),
    "fire": ("火灵根", 1.1, "炼丹有成，暴击更高"),
    "earth": ("土灵根", 1.0, "根基浑厚，气血更足"),
    "mixed": ("杂灵根", 0.9, "平凡起点，胜在什么都能学"),
}
SCENES = {
    "cave": {"name": "青岚洞府", "desc": "灵气浓度适中，最适合打坐。", "scene_bonus": 1.0},
    "valley": {"name": "百草药谷", "desc": "遍地灵草，采药不伤身。", "scene_bonus": 1.0},
    "furnace": {"name": "丹火丹房", "desc": "地火温养，可炼丹。", "scene_bonus": 1.0},
    "forge": {"name": "玄铁器坊", "desc": "灵矿成堆，可炼器。", "scene_bonus": 1.0},
    "abyss": {"name": "幽冥古渊", "desc": "妖物横行，机缘与杀机并存。", "scene_bonus": 1.25},
    "tower": {"name": "天机阁", "desc": "推演天机，可窥命数、接宗门任务。", "scene_bonus": 1.0},
}

exp_need = lambda realm, sub: int(90 * (realm ** 1.5) * (1 + sub * 0.45))  # noqa: E731


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init() -> None:
    with _lock:
        db = _db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cultivators (
                name TEXT PRIMARY KEY,
                realm INTEGER NOT NULL DEFAULT 1,
                sub INTEGER NOT NULL DEFAULT 0,
                exp INTEGER NOT NULL DEFAULT 0,
                comp INTEGER NOT NULL DEFAULT 5,
                luck INTEGER NOT NULL DEFAULT 5,
                root TEXT NOT NULL DEFAULT 'mixed',
                lifespan INTEGER NOT NULL DEFAULT 120,
                stones INTEGER NOT NULL DEFAULT 60,
                herbs INTEGER NOT NULL DEFAULT 0,
                ores INTEGER NOT NULL DEFAULT 0,
                pills INTEGER NOT NULL DEFAULT 0,
                weapon_lv INTEGER NOT NULL DEFAULT 0,
                robe_lv INTEGER NOT NULL DEFAULT 0,
                sect_task INTEGER NOT NULL DEFAULT 3,
                last_tick REAL NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        db.commit()


def _row(name: str) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM cultivators WHERE name=?", ((name or "").strip()[:16],)).fetchone()


def state(name: str) -> dict[str, Any]:
    with _lock:
        init()
        row = _row(name)
        if row is None:
            raise ValueError("角色不存在，请先入道")
        realm, sub = int(row["realm"]), int(row["sub"])
        return {
            "name": row["name"],
            "realm": realm,
            "realm_name": REALMS[min(realm, len(REALMS)) - 1],
            "sub": sub,
            "sub_name": SUB[min(sub, 2)],
            "exp": int(row["exp"]),
            "exp_need": exp_need(realm, sub),
            "comp": int(row["comp"]),
            "luck": int(row["luck"]),
            "root": row["root"],
            "root_name": ROOTS.get(row["root"], ROOTS["mixed"])[0],
            "root_desc": ROOTS.get(row["root"], ROOTS["mixed"])[2],
            "lifespan": int(row["lifespan"]),
            "stones": int(row["stones"]),
            "herbs": int(row["herbs"]),
            "ores": int(row["ores"]),
            "pills": int(row["pills"]),
            "weapon_lv": int(row["weapon_lv"]),
            "robe_lv": int(row["robe_lv"]),
            "sect_task": int(row["sect_task"]),
            "atk": 12 + int(row["weapon_lv"]) * 4 + realm * 2,
            "def": 2 + int(row["robe_lv"]) * 3 + realm,
            "is_max": realm >= len(REALMS) and sub >= 2,
        }


def join(name: str) -> dict[str, Any]:
    with _lock:
        init()
        clean = (name or "").strip()[:16]
        if not clean:
            raise ValueError("道号不能为空")
        db = _db()
        row = _row(clean)
        now = time.time()
        if row is None:
            root = random.choice(list(ROOTS.keys()))
            db.execute(
                "INSERT INTO cultivators (name, comp, luck, root, lifespan, last_tick, created_at) VALUES (?,?,?,?,?,?,?)",
                (clean, random.randint(3, 9), random.randint(3, 9), root, 120, now, now),
            )
            db.commit()
            st = state(clean)
            st["new"] = True
            return st
        return state(clean)


def _save(fields: dict[str, Any], name: str) -> None:
    db = _db()
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE cultivators SET {cols}, last_tick=? WHERE name=?", (*fields.values(), time.time(), name))
    db.commit()


def _cd() -> dict[str, float]:
    return {}


LAST: dict[str, dict[str, float]] = {}


def _cool(name: str, action: str, seconds: float) -> float:
    waits = LAST.setdefault(name, {})
    now = time.time()
    gap = seconds - (now - waits.get(action, 0.0))
    if gap > 0:
        return gap
    waits[action] = now
    return 0.0


def offline_gain(name: str) -> dict[str, Any]:
    """放置收益：最多结算 8 小时打坐。"""
    with _lock:
        row = _row(name)
        if row is None:
            return {"seconds": 0, "exp": 0, "stones": 0}
        now = time.time()
        elapsed = max(0.0, min(8 * 3600, now - float(row["last_tick"])))
        if elapsed < 60:
            return {"seconds": int(elapsed), "exp": 0, "stones": 0}
        per_min = 1 + int(row["realm"]) * 0.8 + int(row["comp"]) * 0.12
        gain = int(per_min * (elapsed / 60))
        stones = int(gain * 0.15)
        st = state(name)
        _save({"exp": st["exp"] + gain, "stones": st["stones"] + stones}, name)
        return {"seconds": int(elapsed), "exp": gain, "stones": stones}


def _root_mul(row: sqlite3.Row, key: str) -> float:
    return ROOTS.get(row["root"], ROOTS["mixed"])[1] if key == "all" else 1.0


# ---------------------------------------------------------------- 场景行动
EVENTS_ABYSS = [
    ("遇到落单妖狼", "fight", 1.0),
    ("误入古修洞府", "treasure", 0.8),
    ("捡到半瓶丹药", "pill", 0.5),
    ("被阴灵缠上", "demon", 1.1),
    ("遇见同为历练的道友", "friend", 0.4),
]


def act(name: str, action: str) -> dict[str, Any]:
    with _lock:
        row = _row(name)
        if row is None:
            raise ValueError("角色不存在")
        st = state(name)
        rnd = random.Random()

        if action == "meditate":
            wait = _cool(name, "meditate", 2.0)
            if wait:
                return {"ok": False, "message": f"气息未匀，{wait:.1f} 秒后再打坐"}
            gain = int((8 + st["realm"] * 4 + st["comp"] * 1.6) * rnd.uniform(0.85, 1.25))
            _save({"exp": st["exp"] + gain}, name)
            return {"ok": True, "message": f"你盘膝吐纳，修为 +{gain}", "gain": gain, "state": state(name)}

        if action == "gather":
            wait = _cool(name, "gather", 4.0)
            if wait:
                return {"ok": False, "message": f"灵草要长一会儿，{wait:.1f} 秒后再采"}
            root_mul = 1.35 if row["root"] == "wood" else 1.0
            herbs = int(rnd.randint(1, 3) * root_mul)
            exp = rnd.randint(2, 6)
            _save({"herbs": st["herbs"] + herbs, "exp": st["exp"] + exp}, name)
            return {"ok": True, "message": f"采得灵草 ×{herbs}，修为 +{exp}", "state": state(name)}

        if action == "mine":
            wait = _cool(name, "mine", 4.0)
            if wait:
                return {"ok": False, "message": f"矿脉尚未松动，{wait:.1f} 秒后再挖"}
            ores = rnd.randint(1, 2)
            _save({"ores": st["ores"] + ores}, name)
            return {"ok": True, "message": f"挖到灵矿 ×{ores}", "state": state(name)}

        if action == "alchemy":
            if st["herbs"] < 3:
                return {"ok": False, "message": "炼丹需要 3 株灵草"}
            rate = 0.62 + st["comp"] * 0.03 + (0.12 if row["root"] == "fire" else 0)
            ok = rnd.random() < min(0.95, rate)
            if ok:
                pills = 1 + (1 if rnd.random() < 0.2 else 0)
                _save({"herbs": st["herbs"] - 3, "pills": st["pills"] + pills}, name)
                return {"ok": True, "message": f"丹成！得丹药 ×{pills}", "state": state(name)}
            _save({"herbs": st["herbs"] - 3}, name)
            return {"ok": True, "message": "炉火不稳，三株灵草化为灰烬……", "state": state(name)}

        if action == "forge":
            if st["ores"] < 3:
                return {"ok": False, "message": "炼器需要 3 块灵矿"}
            slot = rnd.choice(["weapon", "robe"])
            lv = st["weapon_lv"] if slot == "weapon" else st["robe_lv"]
            if lv >= 9:
                return {"ok": False, "message": "法器已至极限，再炼也无用"}
            if rnd.random() < 0.7:
                field = "weapon_lv" if slot == "weapon" else "robe_lv"
                _save({"ores": st["ores"] - 3, field: lv + 1}, name)
                name_cn = "飞剑" if slot == "weapon" else "法袍"
                return {"ok": True, "message": f"{name_cn}淬炼成功，升至 {lv + 1} 阶", "state": state(name)}
            _save({"ores": st["ores"] - 3}, name)
            return {"ok": True, "message": "火候差了一线，灵矿报废", "state": state(name)}

        if action == "train":
            wait = _cool(name, "train", 6.0)
            if wait:
                return {"ok": False, "message": f"伤势未复，{wait:.1f} 秒后再历练"}
            kind, weight = rnd.choice([(k, w) for _n, k, w in EVENTS_ABYSS])
            title = rnd.choice([n for n, k, _w in EVENTS_ABYSS if k == kind])
            if kind == "fight":
                power = st["atk"] + rnd.randint(0, 18)
                enemy = 14 + st["realm"] * 6 + rnd.randint(0, 14)
                if power >= enemy:
                    stones = rnd.randint(8, 22)
                    exp = rnd.randint(12, 30)
                    _save({"stones": st["stones"] + stones, "exp": st["exp"] + exp}, name)
                    return {"ok": True, "message": f"{title}：你一剑斩之，灵石 +{stones}，修为 +{exp}", "state": state(name)}
                loss = int(st["lifespan"] * 0.02) or 1
                _save({"lifespan": max(1, st["lifespan"] - loss)}, name)
                return {"ok": True, "message": f"{title}：不敌遁走，损寿元 {loss} 年", "state": state(name)}
            if kind == "treasure":
                stones = rnd.randint(25, 60)
                exp = rnd.randint(20, 50)
                _save({"stones": st["stones"] + stones, "exp": st["exp"] + exp}, name)
                return {"ok": True, "message": f"{title}：得残卷与灵石 {stones}，修为 +{exp}", "state": state(name)}
            if kind == "pill":
                _save({"pills": st["pills"] + 1}, name)
                return {"ok": True, "message": f"{title}：丹药 +1", "state": state(name)}
            if kind == "demon":
                loss = max(1, int(st["lifespan"] * 0.03))
                _save({"lifespan": st["lifespan"] - loss}, name)
                return {"ok": True, "message": f"{title}：心魔作祟，寿元 -{loss}", "state": state(name)}
            exp = rnd.randint(6, 16)
            _save({"exp": st["exp"] + exp}, name)
            return {"ok": True, "message": f"{title}：论道半日，修为 +{exp}", "state": state(name)}

        if action == "sect":
            if st["sect_task"] <= 0:
                return {"ok": False, "message": "今日宗门任务已尽，明日再来"}
            stones = rnd.randint(15, 34)
            exp = rnd.randint(8, 20)
            _save({"sect_task": st["sect_task"] - 1, "stones": st["stones"] + stones, "exp": st["exp"] + exp}, name)
            return {"ok": True, "message": f"完成宗门差事，灵石 +{stones}，修为 +{exp}", "state": state(name)}

        if action == "seclude":
            wait = _cool(name, "seclude", 30.0)
            if wait:
                return {"ok": False, "message": f"闭关需静心，{wait:.0f} 秒后再来"}
            if st["lifespan"] <= 12:
                return {"ok": False, "message": "寿元将尽，不宜闭关"}
            spend = 6
            gain = int((st["exp_need"] - st["exp"]) * 0.45 + 20)
            _save({"lifespan": st["lifespan"] - spend, "exp": st["exp"] + gain}, name)
            return {"ok": True, "message": f"闭关数日，修为 +{gain}，寿元 -{spend}", "state": state(name)}

        if action == "use_pill":
            if st["pills"] <= 0:
                return {"ok": False, "message": "没有丹药"}
            gain = int(st["exp_need"] * 0.25 + 10)
            heal = 4 if rnd.random() < 0.5 else 0
            _save({"pills": st["pills"] - 1, "exp": st["exp"] + gain, "lifespan": st["lifespan"] + heal}, name)
            extra = f"，寿元 +{heal}" if heal else ""
            return {"ok": True, "message": f"服下丹药，修为 +{gain}{extra}", "state": state(name)}

        raise ValueError(f"未知行动：{action}")


def breakthrough(name: str) -> dict[str, Any]:
    """小境界推进；大境界需渡劫。"""
    with _lock:
        row = _row(name)
        if row is None:
            raise ValueError("角色不存在")
        st = state(name)
        if st["is_max"]:
            return {"ok": False, "message": "已至渡劫后期，此界再无可破"}
        need = st["exp_need"]
        if st["exp"] < need:
            return {"ok": False, "message": f"修为不足（{st['exp']}/{need}）"}
        realm, sub = st["realm"], st["sub"]
        rnd = random.Random()
        if sub < 2:
            rate = 0.9 - sub * 0.08 + st["comp"] * 0.005
            if rnd.random() < min(0.95, rate):
                _save({"sub": sub + 1, "exp": st["exp"] - need}, name)
                return {"ok": True, "message": f"突破成功，晋入{st['realm_name']}{SUB[sub + 1]}", "state": state(name)}
            _save({"exp": int(st["exp"] * 0.8)}, name)
            return {"ok": True, "message": "气机一散，修为倒退两成", "state": state(name)}
        # 渡劫
        rate = 0.45 + st["luck"] * 0.02 + st["comp"] * 0.01 + (0.08 if st["pills"] > 0 else 0)
        if st["pills"] > 0:
            _save({"pills": st["pills"] - 1}, name)
            st = state(name)
        if rnd.random() < min(0.9, rate):
            _save({"realm": realm + 1, "sub": 0, "exp": st["exp"] - need, "lifespan": st["lifespan"] + 40}, name)
            new = REALMS[min(realm + 1, len(REALMS)) - 1]
            return {"ok": True, "message": f"天雷散尽，渡劫成功！晋入{new}期，寿元 +40", "state": state(name)}
        loss_hp = max(1, int(st["lifespan"] * 0.12))
        _save({"exp": int(st["exp"] * 0.7), "lifespan": max(1, st["lifespan"] - loss_hp)}, name)
        return {"ok": True, "message": f"天劫未渡，寿元 -{loss_hp}，修为大跌", "state": state(name)}


def leaderboard(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        init()
        rows = _db().execute(
            "SELECT name, realm, sub, exp, stones FROM cultivators ORDER BY realm DESC, sub DESC, exp DESC LIMIT ?",
            (max(1, min(50, limit)),),
        ).fetchall()
        return [
            {"rank": i + 1, "name": r["name"],
             "realm": f"{REALMS[min(int(r['realm']), 9) - 1]}{SUB[min(int(r['sub']), 2)]}",
             "exp": int(r["exp"]), "stones": int(r["stones"])}
            for i, r in enumerate(rows)
        ]


def scenes() -> dict[str, Any]:
    return {"scenes": [{"key": k, **v} for k, v in SCENES.items()]}
