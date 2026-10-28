"""Incremental WAL reader for wechatauto's decrypted message snapshot.

Upstream ``_open_unlocked`` treats every WAL append as a reason to decrypt the
whole 180+ MB message database again and run ``PRAGMA quick_check``.  With
WeChat writing continuously that took 3-20 seconds per listener poll and made
group replies feel minutes behind.

This module monkey-patches that private method with a conservative fast path:

* main DB changed -> one fast decrypt + WAL merge (upstream does two
  quick_check passes and can take 3-20 seconds);
* WAL only grew -> apply just the new frames to the existing decrypted
  snapshot, then do a cheap schema probe instead of a full quick_check;
* if anything looks wrong -> discard the stamp and let the original rebuild.

The upstream method remains the source of truth and fallback.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from wechatauto.db import (
        STAMP_VERSION,
        WeChatDB,
        _sqlite_text_factory,
    )

    WAL_HEADER_SZ = int(WeChatDB.WAL_HEADER_SZ)
    WAL_FRAME_SZ = int(WeChatDB.WAL_FRAME_SZ)
    original = WeChatDB._open_unlocked

    def _fast_open_unlocked(self: Any, rel: str) -> sqlite3.Connection:
        try:
            conn = _incremental_open(
                self,
                rel,
                STAMP_VERSION,
                WAL_HEADER_SZ,
                WAL_FRAME_SZ,
                _sqlite_text_factory,
            )
            if conn is not None:
                return conn
        except Exception:
            pass
        try:
            conn = _fast_rebuild(
                self,
                rel,
                STAMP_VERSION,
                WAL_HEADER_SZ,
                WAL_FRAME_SZ,
                _sqlite_text_factory,
            )
            if conn is not None:
                return conn
        except Exception:
            pass
        conn = original(self, rel)
        try:
            _remember_wal_state(self, rel, STAMP_VERSION, WAL_HEADER_SZ)
        except Exception:
            pass
        return conn

    WeChatDB._open_unlocked = _fast_open_unlocked
    _INSTALLED = True


def _stamp_path(self: Any, rel: str) -> tuple[str, str]:
    dst = os.path.join(self.workdir, rel.replace(os.sep, "__"))
    return dst, dst + ".stamp"


def _read_stamp(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r") as handle:
            parts = handle.read().split(",")
        return {
            "ver": int(parts[0]),
            "mtime": float(parts[1]),
            "size": int(parts[2]),
            "wal_mtime": float(parts[3]),
            "wal_size": int(parts[4]),
            "applied": int(parts[5]),
        }
    except (OSError, ValueError, IndexError):
        return None


def _write_stamp_version(
    path: str,
    version: int,
    src_mtime: float,
    src_size: int,
    wal_mtime: float,
    wal_size: int,
    applied: int,
) -> None:
    with open(path, "w") as handle:
        handle.write(
            "%d,%r,%d,%r,%d,%d"
            % (version, src_mtime, src_size, wal_mtime, wal_size, applied)
        )


def _wal_header(path: str, header_size: int) -> tuple[bytes, int]:
    with open(path, "rb") as handle:
        header = handle.read(header_size)
    return header, len(header)


def _remember_wal_state(
    self: Any, rel: str, version: int, header_size: int
) -> None:
    """Cache the current WAL salt only when the snapshot includes that WAL."""

    dst, stamp = _stamp_path(self, rel)
    old = _read_stamp(stamp)
    if old is None or old.get("ver") != version or int(old.get("applied", -1)) < 0:
        return
    wal = self._wal_path(rel)
    if not wal or not os.path.exists(wal):
        return
    try:
        header, _ = _wal_header(wal, header_size)
    except OSError:
        return
    salts = getattr(self, "_qunban_wal_salts", None)
    if salts is None:
        salts = self._qunban_wal_salts = {}
    salts[rel] = header[16:24]


def _connect(dst: str, text_factory: Any) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.text_factory = text_factory
    return conn


def _probe(dst: str) -> bool:
    """Cheap check that the merged file is still a readable SQLite DB."""

    try:
        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            conn.execute("PRAGMA schema_version").fetchone()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def _fast_rebuild(
    self: Any,
    rel: str,
    version: int,
    header_size: int,
    frame_size: int,
    text_factory: Any,
) -> sqlite3.Connection | None:
    """Decrypt once and merge the current WAL, skipping upstream's duplicate
    quick_check pass (the main snapshot and merged snapshot were both checked).
    Falls back to the upstream implementation on any suspicious result."""

    key = getattr(self, "_keys", {}).get(rel)
    if not key or len(key) != 32:
        return None
    src = self._db_path(rel)
    wal = self._wal_path(rel)
    dst, stamp_path = _stamp_path(self, rel)
    if not os.path.exists(src):
        return None
    try:
        src_mtime = os.path.getmtime(src)
        src_size = os.path.getsize(src)
        wal_mtime = os.path.getmtime(wal) if wal and os.path.exists(wal) else 0.0
        wal_size = os.path.getsize(wal) if wal and os.path.exists(wal) else 0
    except OSError:
        return None
    tmp = "%s.%d.%x.fast.tmp" % (dst, os.getpid(), threading.get_ident())
    applied = 0
    try:
        for attempt in range(2):
            try:
                self._decrypt_file(src, tmp, key)
                if wal and wal_size > header_size:
                    applied = int(self._merge_wal(tmp, wal, key, 0))
                else:
                    applied = 0
                if not self._check_merged(tmp):
                    continue
                # Re-stat after decrypt+merge.  If WeChat checkpointed or
                # appended while we were building, the result may be a torn
                # generation; retry instead of publishing it.
                try:
                    if (
                        os.path.getmtime(src) != src_mtime
                        or os.path.getsize(src) != src_size
                        or (
                            wal
                            and os.path.exists(wal)
                            and (
                                os.path.getmtime(wal) != wal_mtime
                                or os.path.getsize(wal) != wal_size
                            )
                        )
                    ):
                        continue
                except OSError:
                    continue
                os.replace(tmp, dst)
                _write_stamp_version(
                    stamp_path,
                    version,
                    src_mtime,
                    src_size,
                    wal_mtime,
                    wal_size,
                    applied,
                )
                if wal and wal_size > header_size:
                    try:
                        header, _ = _wal_header(wal, header_size)
                        _cache_salt(self, rel, header[16:24])
                    except Exception:
                        pass
                return _connect(dst, text_factory)
            except Exception:
                continue
    finally:
        for suffix in ("", "-shm", "-wal"):
            try:
                os.remove(tmp + suffix)
            except OSError:
                pass
    return None


def _incremental_open(
    self: Any,
    rel: str,
    version: int,
    header_size: int,
    frame_size: int,
    text_factory: Any,
) -> sqlite3.Connection | None:
    key = getattr(self, "_keys", {}).get(rel)
    if not key or len(key) != 32:
        return None
    src = self._db_path(rel)
    wal = self._wal_path(rel)
    dst, stamp_path = _stamp_path(self, rel)
    if not wal or not os.path.exists(dst) or not os.path.exists(stamp_path):
        return None
    if not os.path.exists(wal):
        return None
    try:
        src_mtime = os.path.getmtime(src)
        src_size = os.path.getsize(src)
        wal_mtime = os.path.getmtime(wal)
        wal_size = os.path.getsize(wal)
    except OSError:
        return None
    old = _read_stamp(stamp_path)
    if old is None or old.get("ver") != version:
        return None
    applied = int(old.get("applied", -1))
    if applied < 0:
        # The previous build fell back to a main-DB-only snapshot.  We cannot
        # know which WAL frames it contains, so let the original rebuild.
        return None
    try:
        header, header_len = _wal_header(wal, header_size)
    except OSError:
        return None
    if header_len < header_size:
        return None
    salt = header[16:24]
    salts = getattr(self, "_qunban_wal_salts", None)
    cached_salt = salts.get(rel) if salts else None
    same_generation = cached_salt is not None and salt == cached_salt
    main_changed = old.get("mtime") != src_mtime or old.get("size") != src_size
    if main_changed:
        # A WeChat checkpoint rewrites pages in the main DB.  The decrypted
        # snapshot cannot be proven to contain those pages, so rebuild.  This
        # is deliberately conservative: serving a stale DB made the Agent
        # answer old messages and made successful sends look like failures.
        return None

    # Main DB is unchanged; a changed WAL generation can be layered from
    # frame 0 onto the snapshot without decrypting the 180 MB main file again.
    if same_generation:
        if wal_size == old.get("wal_size"):
            # Same WAL generation and size: no new frames.  Refresh only the
            # timestamp so the next checkpoint comparison stays cheap.
            try:
                _write_stamp_version(
                    stamp_path,
                    version,
                    src_mtime,
                    src_size,
                    wal_mtime,
                    wal_size,
                    applied,
                )
            except OSError:
                return None
            _cache_salt(self, rel, salt)
            return _connect(dst, text_factory)
        if wal_size < int(old.get("wal_size", 0)):
            # WAL was truncated without a salt change (unusual).  Rebuild.
            return None
        expected_min = header_size + applied * frame_size
        if wal_size <= expected_min:
            return None
        from_frame = applied
        applied_after = applied
    else:
        # New WAL generation (checkpoint reset) or first incremental attempt.
        from_frame = 0
        applied_after = 0

    if wal_size <= header_size:
        # Current WAL is empty.  The snapshot already includes the previous
        # generation; just record the new source/WAL state.
        try:
            _write_stamp_version(
                stamp_path,
                version,
                src_mtime,
                src_size,
                wal_mtime,
                wal_size,
                0,
            )
        except OSError:
            return None
        _cache_salt(self, rel, salt)
        return _connect(dst, text_factory)

    try:
        got = self._merge_wal(dst, wal, key, from_frame)
    except Exception:
        return None
    if got < applied_after:
        return None
    if not _probe(dst):
        try:
            os.remove(stamp_path)
        except OSError:
            pass
        return None
    try:
        _write_stamp_version(
            stamp_path,
            version,
            src_mtime,
            src_size,
            wal_mtime,
            wal_size,
            got,
        )
    except OSError:
        return None
    _cache_salt(self, rel, salt)
    return _connect(dst, text_factory)


def _cache_salt(self: Any, rel: str, salt: bytes) -> None:
    salts = getattr(self, "_qunban_wal_salts", None)
    if salts is None:
        salts = self._qunban_wal_salts = {}
    salts[rel] = salt
