"""End-to-end smoke test against a running server (demo adapter).

Usage:
    .venv\\Scripts\\python.exe scripts/smoke_test.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid

import httpx
from PIL import Image

BASE = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8000")


def wait_server() -> None:
    for _ in range(60):
        try:
            response = httpx.get(f"{BASE}/api/health", timeout=1)
            if response.status_code == 200:
                return
        except Exception:
            time.sleep(0.4)
    raise SystemExit("server is not running")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def main() -> None:
    wait_server()
    with httpx.Client(base_url=BASE, timeout=30) as client:
        print("\n[1] health / status")
        health = client.get("/api/health").json()
        expect(health.get("ok") is True, "health endpoint")
        status = client.get("/api/status").json()
        expect(status["state"] in {"offline", "error"}, "starts offline")
        expect(any(item["id"] == "mock" and item["available"] for item in status["available_adapters"]), "mock adapter available")

        print("\n[2] mock login")
        response = client.post("/api/login", json={"adapter": "mock"})
        expect(response.status_code == 200, "login accepted")
        for _ in range(30):
            status = client.get("/api/status").json()
            if status["state"] == "online":
                break
            time.sleep(0.4)
        expect(status["state"] == "online", "mock account online")
        expect(status["account"]["name"], "account nickname present")
        expect(status["has_qr"] is False, "QR cleared after login")

        print("\n[3] groups and seeded history")
        time.sleep(1.0)
        groups = client.get("/api/groups").json()["groups"]
        expect(len(groups) == 3, "three demo groups synced")
        product = next(group for group in groups if "产品" in group["name"])
        expect(product["agent"]["agent_enabled"] is True, "demo product group has agent enabled")
        expect(product["member_count"] >= 5, "member count present")

        messages = client.get(f"/api/groups/{product['id']}/messages").json()
        expect(len(messages["messages"]) >= 3, "seeded messages stored")
        expect(any(message["sender_name"] == "阿哲" for message in messages["messages"]), "sender nickname resolved")

        members = client.get(f"/api/groups/{product['id']}/members").json()
        expect(members["count"] == 5, "group members synced")
        expect(any(member["nickname"] == "小鹿" for member in members["members"]), "member nicknames readable")

        print("\n[4] manual text + sticker send")
        response = client.post(
            f"/api/groups/{product['id']}/send",
            json={"text": "大家好，这是一条接口测试消息"},
        )
        expect(response.status_code == 200, "manual text sent")
        expect(len(response.json()["messages"]) == 1, "manual message persisted")

        stickers = client.get("/api/stickers").json()
        expect(len(stickers["stickers"]) >= 6, "sample stickers seeded")
        sticker = stickers["stickers"][0]
        response = client.post(
            f"/api/stickers/{sticker['id']}/send",
            json={"group_id": product["id"]},
        )
        expect(response.status_code == 200, "sticker sent")
        expect(response.json()["messages"][0]["kind"] == "sticker", "sticker message stored")

        print("\n[5] sticker upload / edit / delete")
        image = Image.new("RGB", (96, 96), (20, 160, 110))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        upload = client.post(
            "/api/stickers",
            files={"file": (f"test-{uuid.uuid4().hex[:6]}.png", buffer.getvalue(), "image/png")},
            data={"name": "测试表情", "tags": "测试, OK", "description": "smoke test"},
        )
        expect(upload.status_code == 201, "sticker upload")
        uploaded = upload.json()["sticker"]
        expect(uploaded["name"] == "测试表情", "sticker metadata")
        patch = client.patch(f"/api/stickers/{uploaded['id']}", json={"description": "updated"})
        expect(patch.status_code == 200, "sticker edit")
        delete = client.delete(f"/api/stickers/{uploaded['id']}")
        expect(delete.status_code == 200, "sticker delete")

        print("\n[6] agent auto-reply (mock chatter mentions the bot)")
        agent_message = None
        deadline = time.time() + 35
        while time.time() < deadline:
            rows = client.get(f"/api/groups/{product['id']}/messages?limit=100").json()["messages"]
            agent_messages = [row for row in rows if row["is_from_agent"]]
            if agent_messages:
                agent_message = agent_messages[-1]
                break
            time.sleep(1)
        expect(agent_message is not None, "agent replied in demo group")
        if agent_message:
            print("      reply:", json.dumps(agent_message["content"], ensure_ascii=True))
        logs = client.get(f"/api/groups/{product['id']}/logs").json()["logs"]
        expect(any(log["event"] == "reply" for log in logs), "agent reply logged")

        print("\n[7] group config update / settings")
        patch = client.patch(
            f"/api/groups/{product['id']}/config",
            json={
                "agent_enabled": True,
                "trigger_mode": "keyword",
                "keywords": ["总结", "待办"],
                "cooldown_seconds": 5,
                "sticker_enabled": True,
                "quiet_start": "",
                "quiet_end": "",
            },
        )
        expect(patch.status_code == 200, "group config saved")
        group = patch.json()["group"]
        expect(group["agent"]["trigger_mode"] == "keyword", "trigger mode persisted")
        expect(group["agent"]["keywords"] == ["总结", "待办"], "keywords persisted")
        restore = client.patch(
            f"/api/groups/{product['id']}/config",
            json={"trigger_mode": "mention", "cooldown_seconds": 10, "keywords": []},
        )
        expect(restore.status_code == 200, "group config restored")

        settings = client.get("/api/settings").json()["settings"]
        expect("llm_model" in settings and "demo_mode" in settings, "settings readable")
        stats = client.get("/api/stats").json()
        expect(stats["groups"] == 3 and stats["messages"] > 0, "stats readable")

        print("\n[8] unknown adapter rejected")
        response = client.post("/api/login", json={"adapter": "does-not-exist"})
        expect(response.status_code == 400, "unknown adapter returns 400")

        print("\n[9] websocket hello")
        import asyncio
        import websockets

        async def check_ws() -> None:
            async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                payload = json.loads(raw)
                expect(payload["type"] == "hello", "websocket hello received")
                expect(payload["data"]["state"] == "online", "websocket state online")

        asyncio.run(check_ws())

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nSMOKE TEST FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
