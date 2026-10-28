"""A batched variant of wechatauto's database listener.

The upstream listener opens and decrypts every message shard once per
conversation on every poll.  With 20+ readable groups that meant ~100 shard
opens per second and 4-20 second polling rounds.  This subclass opens each
shard once per round, queries every monitored conversation from that shared
connection set, and then reuses the upstream dispatch/watermark machinery.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any

from wechatauto.db import Listener


class FastListener(Listener):
    """Poll many conversations with one open/decrypt per message shard."""

    def _poll_once(self) -> None:  # type: ignore[override]
        callbacks_by_user = list(self._callbacks.items())
        if not callbacks_by_user:
            return
        rels = self.db._message_dbs()
        conns: list[sqlite3.Connection] = []
        try:
            for rel in rels:
                try:
                    conns.append(self.db._open(rel))
                except Exception as exc:
                    sys.stderr.write(
                        "fast-listener shard open failed: %s: %r\n" % (rel, exc)
                    )
            if not conns:
                return
            maps: dict[str, dict[int, str]] = {}
            for conn in conns:
                label = self.db._shard_label(conn)
                if label and label not in maps:
                    try:
                        maps[label] = self.db._name2id_map(conn)
                    except sqlite3.Error:
                        maps[label] = {}

            for user, callbacks in callbacks_by_user:
                tables = self.db._find_msg_tables(user, conns)
                if not tables:
                    continue
                since = max(
                    self._watermark.get(user, 0), self._inflight.get(user, 0)
                )
                rows = self.db._shard_rows(
                    tables,
                    "WHERE sort_seq > ?",
                    (since,),
                    order_ext=self.db._MSG_ORDER_ASC,
                    per_shard_limit=200,
                )
                if not rows:
                    continue
                rows.sort(key=lambda item: item["sort_seq"])
                msgs = [
                    self.db._msg_row_to_dict(row, maps)
                    for row in rows[:200]
                ]
                msgs = [item for item in msgs if item is not None]
                if not msgs:
                    continue
                last_seq = msgs[-1]["sort_seq"]
                with self._wm_lock:
                    if last_seq > self._inflight.get(user, 0):
                        self._inflight[user] = last_seq
                if not callbacks:
                    self._commit_watermark(user, last_seq)
                    continue
                self._dispatch(user, msgs)
        finally:
            closed: set[int] = set()
            for conn in conns:
                if id(conn) in closed:
                    continue
                closed.add(id(conn))
                try:
                    conn.close()
                except Exception:
                    pass
