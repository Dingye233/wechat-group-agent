"""One-off diagnostics for the WeChatFerry adapter.

Run only while the API service is stopped. Prints contact/group/database
information from the logged-in 3.9 client.
"""

from __future__ import annotations

import json
import queue
import time

from wcferry import Wcf


def safe(label, fn):
    try:
        value = fn()
        if isinstance(value, list):
            print(f"{label}: list[{len(value)}]", flush=True)
            print(json.dumps(value[:10], ensure_ascii=False)[:4000], flush=True)
        else:
            print(f"{label}: {value!r}", flush=True)
        return value
    except BaseException as exc:
        print(f"{label}: ERROR {type(exc).__name__}: {exc}", flush=True)
        return None


def main():
    print("creating Wcf", flush=True)
    wcf = Wcf(debug=False, block=False)
    print("is_login:", wcf.is_login(), flush=True)
    print("self:", wcf.get_self_wxid(), flush=True)
    info = safe("user_info", wcf.get_user_info)
    contacts = safe("contacts", wcf.get_contacts)
    if contacts:
        rooms = [c for c in contacts if str(c.get("wxid", "")).endswith("@chatroom")]
        print("contacts chatrooms:", len(rooms), flush=True)
        print(json.dumps(rooms[:10], ensure_ascii=False)[:4000], flush=True)
    safe("friends", wcf.get_friends)
    dbs = safe("dbs", wcf.get_dbs)
    for db in dbs or []:
        if "MicroMsg" in str(db):
            safe(f"tables[{db}]", lambda db=db: wcf.get_tables(db))
    try:
        rows = wcf.query_sql("MicroMsg.db", "SELECT ChatRoomName FROM ChatRoom LIMIT 20;")
        print("ChatRoom rows:", len(rows or []), flush=True)
        print(json.dumps(rows[:20], ensure_ascii=False)[:5000], flush=True)
    except BaseException as exc:
        print("ChatRoom query ERROR:", type(exc).__name__, exc, flush=True)
    safe("self_info_again", wcf.get_user_info)
    try:
        wcf.cleanup()
    except BaseException:
        pass
    print("done", flush=True)


if __name__ == "__main__":
    main()
