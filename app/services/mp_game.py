# -*- coding: utf-8 -*-
"""云隐修仙录 · 联机层：世界聊天 / 组队副本 / 论武 PvP / 排行榜 / 装备（逆水寒式）。

设计原则：
- 不动原游戏的存档与战斗逻辑，联机层是叠加的「社交 + 装备」子系统；
- 战力 = 原游戏境界战力（客户端上报）+ 联机装备加成；
- 数据落 data/rpg.db（新表，和内置文字修仙无关）。
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

SLOTS = [
    ("weapon", "兵刃"), ("head", "冠冕"), ("body", "衣甲"),
    ("wrist", "护腕"), ("belt", "腰带"), ("boots", "战靴"),
]
QUALITY = [
    ("凡品", "#c8d3cf", 1.0), ("良品", "#7ce0a0", 1.35), ("上品", "#7fb6ff", 1.8),
    ("珍品", "#d59bff", 2.4), ("绝品", "#ffb45c", 3.2), ("神兵", "#ff6a5a", 4.4),
]
AFFIXES = [
    ("attack", "攻击"), ("hit", "命中"), ("crit", "会心"), ("break", "破防"),
    ("hp", "气血"), ("def", "防御"), ("block", "格挡"), ("critres", "会心抗性"),
]
NAME_POOL = {
    "weapon": ["青冥剑", "断岳刀", "流云枪", "赤霄剑", "寒江刃"],
    "head": ["玄玉冠", "素心巾", "流华冠", "白羽笠"],
    "body": ["云纹袍", "玄铁甲", "青竹衣", "赤霞战袍"],
    "wrist": ["缠丝护腕", "玄铁护腕", "轻羽护手"],
    "belt": ["玉带", "兽纹腰封", "青锋腰带"],
    "boots": ["踏云靴", "疾风履", "玄铁战靴"],
}
BOSSES = [
    ("赤炎蛟王", 1.0), ("千面尸傀", 1.1), ("玄阴鬼母", 1.2), ("镇狱魔将", 1.35), ("天外遗蜕", 1.5),
]


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
            CREATE TABLE IF NOT EXISTS mp_players (
                name TEXT PRIMARY KEY,
                realm TEXT NOT NULL DEFAULT '练气一层',
                base_cp INTEGER NOT NULL DEFAULT 100,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                stones INTEGER NOT NULL DEFAULT 0,
                gear_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mp_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        db.commit()


def _row(name: str) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM mp_players WHERE name=?", ((name or "").strip()[:16],)).fetchone()


# ---------------------------------------------------------------- 装备
def _item_score(item: dict[str, Any]) -> int:
    q = QUALITY[min(int(item.get("quality", 0)), len(QUALITY) - 1)][2]
    affixes = item.get("affixes") or {}
    base = sum(v for v in affixes.values() if isinstance(v, (int, float)))
    return int(base * q + int(item.get("enhance", 0)) * 12)


def roll_item(slot: str | None = None, quality_bias: float = 0.0) -> dict[str, Any]:
    slot = slot or random.choice([s for s, _ in SLOTS])
    roll = min(0.995, random.random() + quality_bias)
    q = 0
    for i, cut in enumerate([0.45, 0.72, 0.88, 0.96, 0.99]):
        if roll > cut:
            q = i + 1
    q = min(q, len(QUALITY) - 1)
    name = random.choice(NAME_POOL[slot])
    count = 2 + (1 if q >= 2 else 0) + (1 if q >= 4 else 0)
    pool = AFFIXES[:]
    random.shuffle(pool)
    affixes: dict[str, int] = {}
    for key, _cn in pool[:count]:
        if key in ("hp", "def", "block", "critres"):
            affixes[key] = random.randint(6, 16) * (1 + q)
        else:
            affixes[key] = random.randint(2, 6) * (1 + q)
    item = {
        "slot": slot, "name": f"{QUALITY[q][0]}·{name}", "quality": q,
        "affixes": affixes, "enhance": 0, "uid": f"{slot}-{int(time.time() * 1000) % 1000000}-{random.randint(100, 999)}",
    }
    item["score"] = _item_score(item)
    return item


def _gear(name: str) -> dict[str, Any]:
    row = _row(name)
    if row is None:
        return {}
    try:
        data = json.loads(row["gear_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_gear(name: str, gear: dict[str, Any]) -> None:
    db = _db()
    db.execute("UPDATE mp_players SET gear_json=?, updated_at=? WHERE name=?", (json.dumps(gear, ensure_ascii=False), time.time(), name))
    db.commit()


def gear_bonus(gear: dict[str, Any]) -> dict[str, int]:
    total: dict[str, int] = {}
    for item in gear.values():
        if not isinstance(item, dict):
            continue
        mult = 1 + int(item.get("enhance", 0)) * 0.08
        for key, value in (item.get("affixes") or {}).items():
            total[key] = total.get(key, 0) + int(value * mult)
    return total


def combat_power(row: sqlite3.Row) -> int:
    gear = _gear(row["name"])
    bonus = gear_bonus(gear)
    gear_cp = sum(_item_score(i) for i in gear.values() if isinstance(i, dict))
    return int(row["base_cp"] + gear_cp * 1.6 + bonus.get("attack", 0) * 3 + bonus.get("hp", 0) * 0.6)


# ---------------------------------------------------------------- 玩家
def sync_player(name: str, realm: str = "", base_cp: int | None = None) -> dict[str, Any]:
    with _lock:
        init()
        name = (name or "").strip()[:16]
        if not name:
            raise ValueError("道号不能为空")
        db = _db()
        row = _row(name)
        now = time.time()
        if row is None:
            db.execute(
                "INSERT INTO mp_players (name, realm, base_cp, updated_at) VALUES (?,?,?,?)",
                (name, realm or "练气一层", max(50, int(base_cp or 120)), now),
            )
            db.commit()
        else:
            fields = {"updated_at": now}
            if realm:
                fields["realm"] = realm[:12]
            if base_cp:
                fields["base_cp"] = max(50, min(9_999_999, int(base_cp)))
            sets = ", ".join(f"{k}=?" for k in fields)
            db.execute(f"UPDATE mp_players SET {sets} WHERE name=?", (*fields.values(), name))
            db.commit()
        return player_state(name)


def player_state(name: str) -> dict[str, Any]:
    row = _row(name)
    if row is None:
        raise ValueError("角色不存在，请先同步")
    gear = _gear(row["name"])
    return {
        "name": row["name"], "realm": row["realm"], "base_cp": int(row["base_cp"]),
        "cp": combat_power(row), "wins": int(row["wins"]), "losses": int(row["losses"]),
        "stones": int(row["stones"]), "gear": gear, "gear_bonus": gear_bonus(gear),
    }


def leaderboard(limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        init()
        rows = _db().execute("SELECT * FROM mp_players").fetchall()
        ranked = sorted(rows, key=lambda r: combat_power(r), reverse=True)[: max(1, min(50, limit))]
        return [
            {"rank": i + 1, "name": r["name"], "realm": r["realm"], "cp": combat_power(r),
             "wins": int(r["wins"]), "losses": int(r["losses"])}
            for i, r in enumerate(ranked)
        ]


# ---------------------------------------------------------------- 装备操作
def equip_item(name: str, item: dict[str, Any] | None = None, slot: str | None = None) -> dict[str, Any]:
    with _lock:
        player_state(name)
        gear = _gear(name)
        item = item or roll_item(slot)
        old = gear.get(item["slot"])
        gear[item["slot"]] = item
        _save_gear(name, gear)
        return {"ok": True, "item": item, "replaced": old, "state": player_state(name)}


def enhance(name: str, slot: str) -> dict[str, Any]:
    with _lock:
        player_state(name)
        gear = _gear(name)
        item = gear.get(slot)
        if not item:
            raise ValueError("该部位没有装备")
        lv = int(item.get("enhance", 0))
        if lv >= 15:
            return {"ok": False, "message": "已强化至 +15 上限"}
        cost = (lv + 1) * 60
        row = _row(name)
        if int(row["stones"]) < cost:
            return {"ok": False, "message": f"灵石不足，需要 {cost}"}
        rate = max(0.35, 0.95 - lv * 0.045)
        db = _db()
        if random.random() <= rate:
            item["enhance"] = lv + 1
            item["score"] = _item_score(item)
            gear[slot] = item
            _save_gear(name, gear)
            db.execute("UPDATE mp_players SET stones=stones-? WHERE name=?", (cost, name))
            db.commit()
            return {"ok": True, "message": f"强化成功，{item['name']} +{lv + 1}", "item": item, "state": player_state(name)}
        db.execute("UPDATE mp_players SET stones=stones-? WHERE name=?", (cost, name))
        db.commit()
        return {"ok": True, "message": f"强化失败，灵石 -{cost}（装备未掉级）", "state": player_state(name)}


def reroll(name: str, slot: str) -> dict[str, Any]:
    """洗练：重掷词条，保留品质与强化。"""
    with _lock:
        player_state(name)
        gear = _gear(name)
        item = gear.get(slot)
        if not item:
            raise ValueError("该部位没有装备")
        cost = 80
        row = _row(name)
        if int(row["stones"]) < cost:
            return {"ok": False, "message": f"灵石不足，需要 {cost}"}
        fresh = roll_item(slot, quality_bias=0.08)
        item["affixes"] = fresh["affixes"]
        item["score"] = _item_score(item)
        gear[slot] = item
        _save_gear(name, gear)
        db = _db()
        db.execute("UPDATE mp_players SET stones=stones-? WHERE name=?", (cost, name))
        db.commit()
        return {"ok": True, "message": "洗练完成，词条已重铸", "item": item, "state": player_state(name)}


def grant_stones(name: str, amount: int) -> None:
    db = _db()
    db.execute("UPDATE mp_players SET stones=MAX(0, stones+?) WHERE name=?", (int(amount), name))
    db.commit()


# ---------------------------------------------------------------- 组队副本
TEAMS: dict[str, dict[str, Any]] = {}


def team_create(name: str) -> dict[str, Any]:
    with _lock:
        player_state(name)
        for code, team in list(TEAMS.items()):
            if name in team["members"]:
                return {"code": code, **team}
        code = "".join(random.choice("天地玄黄宇宙洪荒") for _ in range(3))
        TEAMS[code] = {"leader": name, "members": [name], "created": time.time()}
        return {"code": code, **TEAMS[code]}


def team_join(name: str, code: str) -> dict[str, Any]:
    with _lock:
        player_state(name)
        team = TEAMS.get((code or "").strip())
        if not team:
            raise ValueError("队伍不存在")
        if len(team["members"]) >= 4:
            raise ValueError("队伍已满（最多 4 人）")
        if name not in team["members"]:
            team["members"].append(name)
        return {"code": code, **team}


def team_leave(name: str) -> dict[str, Any]:
    with _lock:
        for code, team in list(TEAMS.items()):
            if name in team["members"]:
                team["members"].remove(name)
                if not team["members"]:
                    TEAMS.pop(code, None)
                    return {"ok": True, "closed": True}
                if team["leader"] == name:
                    team["leader"] = team["members"][0]
                return {"ok": True, "code": code, **team}
        return {"ok": False, "message": "你还没组队"}


def team_of(name: str) -> dict[str, Any] | None:
    for code, team in TEAMS.items():
        if name in team["members"]:
            return {"code": code, **team}
    return None


def teams() -> list[dict[str, Any]]:
    return [{"code": c, "leader": t["leader"], "members": list(t["members"])} for c, t in TEAMS.items()]


def dungeon(name: str) -> dict[str, Any]:
    """队长开副本：按全队战力与妖王对拼，产出装备与灵石。"""
    with _lock:
        team = team_of(name)
        if not team:
            raise ValueError("你还没组队")
        if team["leader"] != name:
            raise ValueError("只有队长能开副本")
        members = list(team["members"])
        states = [player_state(m) for m in members]
        total_cp = sum(s["cp"] for s in states)
        boss, scale = random.choice(BOSSES)
        boss_cp = int((total_cp / max(1, len(members))) * 1.35 * scale)
        log: list[str] = [f"⚔️ 队伍【{team['code']}】闯入秘境，遭遇 {boss}（战力 {boss_cp}）"]
        hp = boss_cp
        rounds = 0
        while hp > 0 and rounds < 12:
            rounds += 1
            dmg = int(total_cp * random.uniform(0.09, 0.16))
            hp -= dmg
            log.append(f"第 {rounds} 回合：全队合击 {dmg}（妖王余 {max(0, hp)}）")
            if hp <= 0:
                break
            hurt = int(boss_cp * random.uniform(0.05, 0.1))
            log.append(f"　{boss} 反击，全队承伤 {hurt}")
        victory = hp <= 0
        rewards: list[dict[str, Any]] = []
        db = _db()
        for st in states:
            if victory:
                stones = random.randint(40, 90)
                item = roll_item(quality_bias=0.12 + random.random() * 0.1)
                grant_stones(st["name"], stones)
                db.execute("UPDATE mp_players SET wins=wins+1 WHERE name=?", (st["name"],))
                db.commit()
                rewards.append({"name": st["name"], "stones": stones, "item": item})
            else:
                db.execute("UPDATE mp_players SET losses=losses+1 WHERE name=?", (st["name"],))
                db.commit()
                rewards.append({"name": st["name"], "stones": 0, "item": None})
        if victory:
            log.append(f"🏆 {boss} 伏诛，全队分战利品")
            TEAMS.pop(team["code"], None)
        else:
            log.append("💀 不敌，队伍暂退")
        _record("dungeon", f"{name} 的队伍{'讨伐' if victory else '折戟'}【{boss}】")
        return {"ok": True, "victory": victory, "boss": boss, "boss_cp": boss_cp,
                "rounds": rounds, "log": log, "rewards": rewards}


# ---------------------------------------------------------------- 论武 PvP
def duel(a: str, b: str) -> dict[str, Any]:
    with _lock:
        if a == b:
            raise ValueError("不能和自己论武")
        sa = player_state(a)
        sb = player_state(b)
        pa = sa["cp"] * random.uniform(0.88, 1.12)
        pb = sb["cp"] * random.uniform(0.88, 1.12)
        log = [f"🥋 {a}（战力 {sa['cp']}）与 {b}（战力 {sb['cp']}）论武"]
        winner, loser = (a, b) if pa >= pb else (b, a)
        db = _db()
        db.execute("UPDATE mp_players SET wins=wins+1, stones=stones+? WHERE name=?", (random.randint(15, 40), winner))
        db.execute("UPDATE mp_players SET losses=losses+1 WHERE name=?", (loser,))
        db.commit()
        log.append(f"🏆 {winner} 胜，{loser} 败")
        _record("duel", f"{winner} 论武胜 {loser}")
        return {"ok": True, "winner": winner, "loser": loser,
                "log": log, "winner_cp": max(sa["cp"], sb["cp"]), "loser_cp": min(sa["cp"], sb["cp"])}


# ---------------------------------------------------------------- 记录
def _record(kind: str, text: str) -> None:
    db = _db()
    db.execute("INSERT INTO mp_records (kind, text, created_at) VALUES (?,?,?)", (kind, text, time.time()))
    db.commit()


def records(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        init()
        rows = _db().execute("SELECT kind, text, created_at FROM mp_records ORDER BY id DESC LIMIT ?",
                             (max(1, min(50, limit)),)).fetchall()
        return [dict(r) for r in rows]


def seed_stones(name: str, amount: int = 500) -> dict[str, Any]:
    with _lock:
        player_state(name)
        grant_stones(name, amount)
        return player_state(name)
