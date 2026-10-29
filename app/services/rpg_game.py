# -*- coding: utf-8 -*-
"""修仙 RPG 服务端引擎（群伴 · 网页版 RPG）。

一个轻量、可持久化、线程安全的游戏内核：
- 境界/修为/突破
- 打坐修炼、探索秘境、自动战斗、疗伤
- 排行榜、世界事件

数据落在 data/rpg.db（独立的 sqlite，不动群伴主库）。
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REALMS = ["练气", "筑基", "金丹", "元婴", "化神", "炼虚", "合体", "大乘", "渡劫"]
MAX_REALM = len(REALMS)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "rpg.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

# 每个玩家最近一次动作的时间，用于简单冷却（内存态，重启即清空）
_last_action: dict[str, dict[str, float]] = {}

COOLDOWNS = {"cultivate": 2.0, "explore": 6.0, "breakthrough": 3.0, "heal": 3.0}


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    with _lock:
        db = _db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                realm INTEGER NOT NULL DEFAULT 1,
                exp INTEGER NOT NULL DEFAULT 0,
                stones INTEGER NOT NULL DEFAULT 30,
                hp INTEGER NOT NULL DEFAULT 100,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                sect TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS world_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        db.commit()


def exp_needed(realm: int) -> int:
    return int(80 * (realm ** 1.55))


def max_hp(realm: int) -> int:
    return 100 + (realm - 1) * 45


def realm_name(realm: int) -> str:
    index = max(1, min(MAX_REALM, realm)) - 1
    return REALMS[index]


def _row_to_state(row: sqlite3.Row) -> dict[str, Any]:
    realm = int(row["realm"])
    return {
        "name": row["name"],
        "realm": realm,
        "realm_name": realm_name(realm),
        "exp": int(row["exp"]),
        "exp_needed": exp_needed(realm),
        "stones": int(row["stones"]),
        "hp": int(row["hp"]),
        "max_hp": max_hp(realm),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "sect": row["sect"] or "",
        "is_max_realm": realm >= MAX_REALM,
    }


def _fetch(name: str) -> sqlite3.Row | None:
    db = _db()
    return db.execute("SELECT * FROM players WHERE name = ?", (name,)).fetchone()


def _save(state: dict[str, Any]) -> None:
    db = _db()
    db.execute(
        "UPDATE players SET realm=?, exp=?, stones=?, hp=?, wins=?, losses=?, updated_at=? WHERE name=?",
        (
            int(state["realm"]), int(state["exp"]), int(state["stones"]), int(state["hp"]),
            int(state["wins"]), int(state["losses"]), time.time(), state["name"],
        ),
    )
    db.commit()


def log_event(name: str, kind: str, text: str) -> dict[str, Any]:
    db = _db()
    now = time.time()
    db.execute(
        "INSERT INTO world_events (name, kind, text, created_at) VALUES (?,?,?,?)",
        (name, kind, text, now),
    )
    db.commit()
    return {"name": name, "kind": kind, "text": text, "at": now}


def join(name: str) -> dict[str, Any]:
    """创建或读取角色（局域网内无密码，名字即身份）。"""
    name = (name or "").strip()[:16]
    if not name:
        raise ValueError("道号不能为空")
    with _lock:
        init_db()
        db = _db()
        row = _fetch(name)
        if row is None:
            now = time.time()
            db.execute(
                "INSERT INTO players (name, realm, exp, stones, hp, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (name, 1, 0, 30, max_hp(1), now, now),
            )
            db.commit()
            row = _fetch(name)
            state = _row_to_state(row)
            state["new"] = True
            return state
        return _row_to_state(row)


def get_state(name: str) -> dict[str, Any]:
    with _lock:
        init_db()
        row = _fetch((name or "").strip()[:16])
        if row is None:
            raise ValueError("角色不存在")
        return _row_to_state(row)


def _cool(name: str, action: str) -> float:
    now = time.time()
    waits = _last_action.setdefault(name, {})
    last = waits.get(action, 0.0)
    gap = COOLDOWNS.get(action, 0.0) - (now - last)
    if gap > 0:
        return gap
    waits[action] = now
    return 0.0


def cultivate(name: str) -> dict[str, Any]:
    with _lock:
        row = _fetch(name)
        if row is None:
            raise ValueError("角色不存在")
        wait = _cool(name, "cultivate")
        if wait > 0:
            return {"ok": False, "cooldown": round(wait, 1), "message": f"灵气尚未聚拢，{wait:.1f} 秒后再来"}
        state = _row_to_state(row)
        gain = random.randint(6, 12) + state["realm"] * 3
        state["exp"] += gain
        _save(state)
        text = f"{name} 打坐吐纳，修为 +{gain}"
        return {"ok": True, "gain": gain, "state": get_state(name), "message": text}


def breakthrough(name: str) -> dict[str, Any]:
    with _lock:
        row = _fetch(name)
        if row is None:
            raise ValueError("角色不存在")
        wait = _cool(name, "breakthrough")
        if wait > 0:
            return {"ok": False, "cooldown": round(wait, 1), "message": f"境界未稳，{wait:.1f} 秒后再冲关"}
        state = _row_to_state(row)
        if state["is_max_realm"]:
            return {"ok": False, "message": "已至渡劫，前路需自悟"}
        need = state["exp_needed"]
        if state["exp"] < need:
            return {"ok": False, "message": f"修为不足（{state['exp']}/{need}），先把气机夯实"}
        chance = max(0.45, 0.9 - state["realm"] * 0.05)
        if random.random() <= chance:
            state["realm"] += 1
            state["exp"] = max(0, state["exp"] - need)
            state["hp"] = max_hp(state["realm"])
            _save(state)
            text = f"⚡ {name} 突破成功，晋入 {realm_name(state['realm'])} 期！"
            return {"ok": True, "success": True, "state": get_state(name), "message": text}
        loss = int(state["exp"] * 0.25)
        state["exp"] = max(0, state["exp"] - loss)
        state["hp"] = max(1, int(state["hp"] * 0.5))
        _save(state)
        text = f"💥 {name} 冲关失败，修为倒退 {loss}，气血大损"
        return {"ok": True, "success": False, "state": get_state(name), "message": text}


ENEMIES = [
    ("山魈", 0.8), ("赤练蛇", 0.9), ("落单散修", 1.0), ("黑风寨匪修", 1.15),
    ("古墓阴灵", 1.25), ("秘境守阵傀儡", 1.4), ("化形妖狐", 1.6),
]


def _battle(power: int, enemy_power: int) -> tuple[bool, list[str]]:
    log: list[str] = []
    php, ehp = power * 10, enemy_power * 10
    for rnd in range(1, 9):
        pdmg = random.randint(6, 14) + power // 2
        edmg = random.randint(5, 13) + enemy_power // 2
        ehp -= pdmg
        log.append(f"第 {rnd} 回合：你斩出 {pdmg}，敌受创（余 {max(0, ehp)}）")
        if ehp <= 0:
            return True, log
        php -= edmg
        log.append(f"　敌反击 {edmg}，你气血余 {max(0, php)}")
        if php <= 0:
            return False, log
    return php > ehp, log


def explore(name: str) -> dict[str, Any]:
    with _lock:
        row = _fetch(name)
        if row is None:
            raise ValueError("角色不存在")
        wait = _cool(name, "explore")
        if wait > 0:
            return {"ok": False, "cooldown": round(wait, 1), "message": f"秘境入口尚在凝聚，{wait:.1f} 秒后再探"}
        state = _row_to_state(row)
        roll = random.random()
        if roll < 0.18:
            gain = random.randint(20, 40)
            stones = random.randint(5, 15)
            state["exp"] += gain
            state["stones"] += stones
            _save(state)
            return {"ok": True, "kind": "灵草", "battle": [],
                    "message": f"你在崖壁采得灵草，修为 +{gain}，灵石 +{stones}",
                    "state": get_state(name)}
        if roll < 0.26:
            gain = random.randint(40, 90) + state["realm"] * 10
            state["exp"] += gain
            _save(state)
            return {"ok": True, "kind": "秘境", "battle": [],
                    "message": f"你误入上古洞府，参悟残碑，修为 +{gain}",
                    "state": get_state(name)}
        if roll < 0.34:
            stones = random.randint(10, 25)
            state["stones"] += stones
            _save(state)
            return {"ok": True, "kind": "机缘", "battle": [],
                    "message": f"你在坊市捡漏，灵石 +{stones}",
                    "state": get_state(name)}

        enemy, scale = random.choice(ENEMIES)
        power = state["realm"] * 22 + int(state["hp"] / 12)
        enemy_power = int(power * scale * random.uniform(0.75, 1.15))
        won, battle = _battle(power, enemy_power)
        if won:
            gain = int(enemy_power * 1.6) + random.randint(5, 20)
            stones = random.randint(3, 12)
            state["exp"] += gain
            state["stones"] += stones
            state["wins"] += 1
            state["hp"] = max(1, state["hp"] - random.randint(5, 20))
            _save(state)
            return {"ok": True, "kind": "战斗", "battle": battle,
                    "message": f"击败 {enemy}！修为 +{gain}，灵石 +{stones}",
                    "state": get_state(name)}
        state["losses"] += 1
        state["hp"] = max(1, int(state["hp"] * 0.4))
        state["exp"] = max(0, state["exp"] - random.randint(5, 15))
        _save(state)
        return {"ok": True, "kind": "战斗", "battle": battle,
                "message": f"不敌 {enemy}，重伤遁走……",
                "state": get_state(name)}


def heal(name: str) -> dict[str, Any]:
    with _lock:
        row = _fetch(name)
        if row is None:
            raise ValueError("角色不存在")
        wait = _cool(name, "heal")
        if wait > 0:
            return {"ok": False, "cooldown": round(wait, 1), "message": f"丹药未成，{wait:.1f} 秒后再服"}
        state = _row_to_state(row)
        cost = 20
        if state["stones"] < cost:
            return {"ok": False, "message": f"灵石不足（需 {cost}）"}
        state["stones"] -= cost
        state["hp"] = state["max_hp"]
        _save(state)
        return {"ok": True, "message": f"服下回春丹，气血全满（灵石 -{cost}）", "state": get_state(name)}


def leaderboard(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        init_db()
        db = _db()
        rows = db.execute(
            "SELECT * FROM players ORDER BY realm DESC, exp DESC LIMIT ?", (max(1, min(50, limit)),)
        ).fetchall()
        return [
            {
                "rank": index + 1,
                "name": row["name"],
                "realm_name": realm_name(int(row["realm"])),
                "realm": int(row["realm"]),
                "exp": int(row["exp"]),
                "wins": int(row["wins"]),
            }
            for index, row in enumerate(rows)
        ]


def world_log(limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        init_db()
        db = _db()
        rows = db.execute(
            "SELECT name, kind, text, created_at FROM world_events ORDER BY id DESC LIMIT ?",
            (max(1, min(100, limit)),),
        ).fetchall()
        return [dict(row) for row in rows]


def stats() -> dict[str, Any]:
    with _lock:
        init_db()
        db = _db()
        row = db.execute("SELECT COUNT(*) AS n, MAX(realm) AS top FROM players").fetchone()
        return {"players": int(row["n"] or 0), "top_realm": realm_name(int(row["top"] or 1))}


# --------------------------------------------------------------------------
# 组队副本
# --------------------------------------------------------------------------

_parties: dict[str, dict[str, Any]] = {}
PARTY_CODE_ALPHABET = "天地玄黄宇宙洪荒"

BOSSES = [
    ("千年树妖", 1.0), ("赤焰蛟龙", 1.15), ("青丘狐王", 1.25), ("万骨尸王", 1.35), ("堕仙残魂", 1.5),
]


def _new_code() -> str:
    for _ in range(80):
        code = "".join(random.choice(PARTY_CODE_ALPHABET) for _ in range(3))
        if code not in _parties:
            return code
    raise ValueError("队伍太多，稍后再试")


def party_of(name: str) -> dict[str, Any] | None:
    for code, party in _parties.items():
        if name in party["members"]:
            return {"code": code, **party}
    return None


def create_party(name: str) -> dict[str, Any]:
    with _lock:
        get_state(name)
        current = party_of(name)
        if current:
            return current
        code = _new_code()
        _parties[code] = {"leader": name, "members": [name], "created": time.time()}
        return {"code": code, **_parties[code]}


def join_party(name: str, code: str) -> dict[str, Any]:
    with _lock:
        get_state(name)
        code = (code or "").strip()
        party = _parties.get(code)
        if not party:
            raise ValueError("查无此队，编号对不对？")
        if len(party["members"]) >= 4:
            raise ValueError("队伍已满（最多 4 人）")
        if name in party["members"]:
            return {"code": code, **party}
        other = party_of(name)
        if other:
            raise ValueError(f"你已在队伍 {other['code']} 中")
        party["members"].append(name)
        return {"code": code, **party}


def leave_party(name: str) -> dict[str, Any]:
    with _lock:
        for code, party in list(_parties.items()):
            if name in party["members"]:
                party["members"].remove(name)
                if not party["members"]:
                    _parties.pop(code, None)
                    return {"code": code, "left": True, "closed": True}
                if party["leader"] == name:
                    party["leader"] = party["members"][0]
                return {"code": code, "left": True, "closed": False, **party}
        return {"left": False, "message": "你还没组队"}


def list_parties() -> list[dict[str, Any]]:
    with _lock:
        return [
            {"code": code, "leader": party["leader"], "members": list(party["members"]),
             "size": len(party["members"])}
            for code, party in _parties.items()
        ]


def start_dungeon(name: str) -> dict[str, Any]:
    """队长开副本：服务端和妖王打回合，全队按输出分奖励。"""
    with _lock:
        party = party_of(name)
        if not party:
            raise ValueError("你还没组队")
        if party["leader"] != name:
            raise ValueError("只有队长能开副本")
        members = list(party["members"])
        states = {m: get_state(m) for m in members}
        avg_realm = sum(s["realm"] for s in states.values()) / max(1, len(states))
        boss, scale = random.choice(BOSSES)
        boss_hp = int(140 * len(members) * (avg_realm ** 0.9) * scale)
        boss_max = boss_hp
        hp = {m: states[m]["hp"] for m in members}
        log: list[str] = [f"⚔️ 队伍 {party['code']} 闯入妖穴，遭遇【{boss}】（气血 {boss_hp}）"]
        round_no = 0
        while boss_hp > 0 and any(v > 0 for v in hp.values()) and round_no < 30:
            round_no += 1
            for m in members:
                if hp[m] <= 0:
                    continue
                dmg = random.randint(10, 22) + states[m]["realm"] * 8
                boss_hp -= dmg
                log.append(f"第{round_no}回合 {m} 斩出 {dmg}（妖王余 {max(0, boss_hp)}）")
                if boss_hp <= 0:
                    break
            if boss_hp <= 0:
                break
            targets = [m for m in members if hp[m] > 0]
            if not targets:
                break
            victim = random.choice(targets)
            bdmg = random.randint(8, 18) + int(avg_realm * 6)
            hp[victim] -= bdmg
            log.append(f"　妖王反击 {victim}，受创 {bdmg}（余 {max(0, hp[victim])}）")

        victory = boss_hp <= 0
        rewards: list[dict[str, Any]] = []
        for m in members:
            state = get_state(m)
            if victory:
                exp = int(boss_max / max(1, len(members)) * 0.35) + random.randint(15, 40)
                stones = random.randint(10, 26)
                state["exp"] += exp
                state["stones"] += stones
                state["wins"] += 1
                state["hp"] = max(1, min(state["max_hp"], hp[m] if hp[m] > 0 else 1))
                _save(state)
                rewards.append({"name": m, "exp": exp, "stones": stones, "down": hp[m] <= 0})
            else:
                state["losses"] += 1
                state["hp"] = max(1, int(state["max_hp"] * 0.35))
                state["exp"] = max(0, state["exp"] - 10)
                _save(state)
                rewards.append({"name": m, "exp": -10, "stones": 0, "down": hp[m] <= 0})
        if victory:
            log.append(f"🏆 妖王【{boss}】伏诛！全队分享战果")
            _parties.pop(party["code"], None)
        else:
            log.append("💀 队伍不敌，暂退妖穴")
        return {"ok": True, "victory": victory, "boss": boss, "boss_hp": boss_max,
                "rounds": round_no, "log": log, "rewards": rewards}


# --------------------------------------------------------------------------
# 论道（PvP）
# --------------------------------------------------------------------------

def duel(a: str, b: str) -> dict[str, Any]:
    with _lock:
        if a == b:
            raise ValueError("不能和自己论道")
        sa = get_state(a)
        sb = get_state(b)
        power_a = sa["realm"] * 26 + sa["hp"] // 10
        power_b = sb["realm"] * 26 + sb["hp"] // 10
        log: list[str] = [f"🥋 {a} 与 {b} 论道，双方起手"]
        hp_a, hp_b = sa["hp"], sb["hp"]
        round_no = 0
        while hp_a > 0 and hp_b > 0 and round_no < 10:
            round_no += 1
            dmg_a = random.randint(6, 14) + power_a // 3
            hp_b -= dmg_a
            log.append(f"第{round_no}回合 {a} 抢先出手 {dmg_a}（{b} 余 {max(0, hp_b)}）")
            if hp_b <= 0:
                break
            dmg_b = random.randint(6, 14) + power_b // 3
            hp_a -= dmg_b
            log.append(f"　{b} 还手 {dmg_b}（{a} 余 {max(0, hp_a)}）")
        winner, loser = (a, b) if hp_b <= 0 or (hp_a > hp_b and hp_a > 0) else (b, a)
        win_state = get_state(winner)
        lose_state = get_state(loser)
        exp_gain = 15 + int(lose_state["realm"] * 8)
        stones = random.randint(5, 15)
        win_state["exp"] += exp_gain
        win_state["stones"] += stones
        win_state["wins"] += 1
        win_state["hp"] = max(1, sa["hp"] if winner == a else sb["hp"])
        lose_state["losses"] += 1
        lose_state["hp"] = max(1, int(lose_state["max_hp"] * 0.45))
        lose_state["exp"] = max(0, lose_state["exp"] - 8)
        _save(win_state)
        _save(lose_state)
        text = f"🏆 {winner} 论道胜 {loser}，修为 +{exp_gain}，灵石 +{stones}"
        log.append(text)
        log_event(winner, "duel", text)
        return {"ok": True, "winner": winner, "loser": loser, "log": log,
                "reward": {"exp": exp_gain, "stones": stones},
                "state": get_state(winner)}


# --------------------------------------------------------------------------
# 后台管理
# --------------------------------------------------------------------------

def admin_players(query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    with _lock:
        init_db()
        db = _db()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        like = f"%{(query or '').strip()}%"
        rows = db.execute(
            "SELECT * FROM players WHERE name LIKE ? ORDER BY realm DESC, exp DESC LIMIT ? OFFSET ?",
            (like, limit, offset),
        ).fetchall()
        total = db.execute("SELECT COUNT(*) AS n FROM players WHERE name LIKE ?", (like,)).fetchone()["n"]
        players = []
        for row in rows:
            state = _row_to_state(row)
            state["created_at"] = row["created_at"]
            state["updated_at"] = row["updated_at"]
            players.append(state)
        return {"players": players, "total": int(total or 0), "limit": limit, "offset": offset}


def admin_update_player(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        row = _fetch((name or "").strip()[:16])
        if row is None:
            raise ValueError("角色不存在")
        state = _row_to_state(row)
        for key in ("realm", "exp", "stones", "hp"):
            if key in fields and fields[key] is not None and str(fields[key]) != "":
                try:
                    value = int(fields[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} 必须是整数") from None
                if key == "realm":
                    value = max(1, min(MAX_REALM, value))
                elif key == "exp":
                    value = max(0, value)
                elif key == "stones":
                    value = max(0, value)
                elif key == "hp":
                    value = max(1, min(max_hp(state["realm"]), value))
                state[key] = value
        _save(state)
        if "sect" in fields:
            db = _db()
            db.execute("UPDATE players SET sect=? WHERE name=?", (str(fields["sect"])[:24], state["name"]))
            db.commit()
        return get_state(state["name"])


def admin_delete_player(name: str) -> dict[str, Any]:
    with _lock:
        db = _db()
        row = _fetch((name or "").strip()[:16])
        if row is None:
            raise ValueError("角色不存在")
        db.execute("DELETE FROM players WHERE name=?", (row["name"],))
        db.commit()
        return {"deleted": row["name"]}


def admin_reset_all(keep_events: bool = False) -> dict[str, Any]:
    with _lock:
        db = _db()
        db.execute("DELETE FROM players")
        if not keep_events:
            db.execute("DELETE FROM world_events")
        db.commit()
        _parties.clear()
        _last_action.clear()
        return {"ok": True, "players": 0, "events_kept": bool(keep_events)}


def admin_overview() -> dict[str, Any]:
    with _lock:
        init_db()
        db = _db()
        row = db.execute(
            "SELECT COUNT(*) AS n, MAX(realm) AS top, SUM(stones) AS stones, SUM(wins) AS wins, SUM(losses) AS losses FROM players"
        ).fetchone()
        events = db.execute("SELECT COUNT(*) AS n FROM world_events").fetchone()["n"]
        latest = db.execute(
            "SELECT name, kind, text, created_at FROM world_events ORDER BY id DESC LIMIT 12"
        ).fetchall()
        return {
            "players": int(row["n"] or 0),
            "top_realm": realm_name(int(row["top"] or 1)),
            "stones": int(row["stones"] or 0),
            "wins": int(row["wins"] or 0),
            "losses": int(row["losses"] or 0),
            "events": int(events or 0),
            "parties": [
                {"code": c, "leader": p["leader"], "members": list(p["members"])}
                for c, p in _parties.items()
            ],
            "latest_events": [dict(e) for e in latest],
        }
