from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import qrcode
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BiliAccount, utcnow

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

try:  # bilibili-api-python is optional at import time.
    from bilibili_api import (
        Credential,
        comment,
        favorite_list,
        hot,
        login_v2,
        search,
        user,
        video,
    )
    from bilibili_api.utils.geetest import Geetest, GeetestType

    _BILI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - only when dependency is missing
    Credential = None  # type: ignore[assignment]
    comment = None  # type: ignore[assignment]
    favorite_list = None  # type: ignore[assignment]
    hot = None  # type: ignore[assignment]
    login_v2 = None  # type: ignore[assignment]
    search = None  # type: ignore[assignment]
    user = None  # type: ignore[assignment]
    video = None  # type: ignore[assignment]
    Geetest = None  # type: ignore[assignment]
    GeetestType = None  # type: ignore[assignment]
    _BILI_IMPORT_ERROR = exc

logger = logging.getLogger("bilibili")
QR_DIR = Path("data") / "media" / "assistant"
AUDIO_DIR = Path("data") / "media" / "bili" / "audio"
QR_SESSIONS: dict[str, dict[str, Any]] = {}
PASSWORD_SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_TTL = 180.0
_ASR_MODELS: dict[str, Any] = {}
_ASR_LOCK = asyncio.Lock()


def _require() -> None:
    if _BILI_IMPORT_ERROR is not None:
        raise RuntimeError(
            "B站依赖未安装：请执行 pip install bilibili-api-python"
        )


def _clean(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _video_item(item: dict[str, Any]) -> dict[str, Any]:
    bvid = str(item.get("bvid") or "").strip()
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    title = _clean(item.get("title"))
    author = _clean(item.get("author") or owner.get("name"))
    play = item.get("play") or stat.get("view") or 0
    duration = item.get("duration")
    if isinstance(duration, (int, float)):
        duration_text = f"{int(duration) // 60}:{int(duration) % 60:02d}"
    else:
        duration_text = str(duration or "")
    return {
        "bvid": bvid,
        "title": title,
        "author": author,
        "play": int(play or 0),
        "duration": duration_text,
        "cover": str(item.get("pic") or item.get("cover") or ""),
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
    }


def _normalize_info(info: dict[str, Any]) -> dict[str, Any]:
    item = _video_item(info)
    item.update(
        {
            "description": _clean(info.get("desc")),
            "pubdate": info.get("pubdate"),
            "like": int((info.get("stat") or {}).get("like") or 0),
            "coin": int((info.get("stat") or {}).get("coin") or 0),
            "favorite": int((info.get("stat") or {}).get("favorite") or 0),
            "reply": int((info.get("stat") or {}).get("reply") or 0),
        }
    )
    return item


_VALID_CACHE: dict[str, tuple[float, bool]] = {}
_VALID_TTL_SECONDS = 60.0


def _cache_key(account_key: str = "") -> str:
    return account_key or "__default__"


def _is_login_error(exc: BaseException) -> bool:
    text = str(exc)
    return (
        "-101" in text
        or "账号未登录" in text
        or "not logged in" in text.lower()
        or "未登录" in text
    )


async def _mark_account_invalid(row: BiliAccount, account_key: str = "") -> None:
    _VALID_CACHE[_cache_key(account_key or row.account_key)] = (
        time.monotonic(),
        False,
    )
    try:
        async with SessionLocal() as db:
            db_row = await db.get(BiliAccount, row.id)
            if db_row is not None:
                db_row.enabled = False
                db_row.updated_at = utcnow()
                await db.commit()
    except Exception:
        pass


async def _row_is_valid(row: BiliAccount, *, force: bool = False) -> bool:
    key = _cache_key(row.account_key)
    now = time.monotonic()
    cached = _VALID_CACHE.get(key)
    if cached and not force and now - cached[0] < _VALID_TTL_SECONDS:
        return cached[1]
    try:
        valid = bool(await _credential(row).check_valid())
    except Exception:
        valid = False
    _VALID_CACHE[key] = (now, valid)
    if not valid:
        await _mark_account_invalid(row)
    return valid


async def _run_authed(coro: Any, *, account_key: str, action: str) -> Any:
    """Run a write API and translate B站 login failures into a clear error."""
    try:
        return await coro
    except Exception as exc:
        if _is_login_error(exc):
            row = await _load_row(account_key)
            if row is not None:
                await _mark_account_invalid(row, account_key)
            raise RuntimeError(
                f"B站登录已失效（{action}失败：账号未登录），"
                "请在「助手 → B站」重新扫码登录"
            ) from exc
        raise


def _account_payload(row: BiliAccount | None) -> dict[str, Any]:
    cookie_count = 0
    if row is not None:
        try:
            parsed = json.loads(row.cookie_json or "{}")
            if isinstance(parsed, dict):
                cookie_count = len(parsed)
        except Exception:
            cookie_count = 0
    if row is None or not row.sessdata:
        return {
            "logged_in": False,
            "expired": False,
            "name": "",
            "mid": "",
            "sessdata_hint": "",
            "message": "",
            "cookie_saved": False,
            "cookie_count": 0,
        }
    return {
        "logged_in": True,
        "expired": False,
        "name": row.name or "",
        "mid": row.mid or "",
        "sessdata_hint": f"••••{row.sessdata[-6:]}" if len(row.sessdata) >= 6 else "••••",
        "message": "",
        "cookie_saved": cookie_count > 0,
        "cookie_count": cookie_count,
    }


async def _load_row(account_key: str = "") -> BiliAccount | None:
    try:
        async with SessionLocal() as db:
            stmt = select(BiliAccount).order_by(BiliAccount.id.desc()).limit(1)
            if account_key:
                stmt = (
                    select(BiliAccount)
                    .where(BiliAccount.account_key == account_key)
                    .order_by(BiliAccount.id.desc())
                    .limit(1)
                )
            return (await db.execute(stmt)).scalar_one_or_none()
    except Exception:
        # The table is created on startup; this keeps anonymous read APIs
        # usable during the first boot or before a migration.
        return None


def _credential(row: BiliAccount) -> Any:
    _require()
    cookie_map: dict[str, str] = {}
    raw_cookies = str(getattr(row, "cookie_json", "") or "")
    if raw_cookies:
        try:
            parsed = json.loads(raw_cookies)
            if isinstance(parsed, dict):
                cookie_map = {
                    str(key): str(value)
                    for key, value in parsed.items()
                    if value is not None and str(value) != ""
                }
        except Exception:
            cookie_map = {}
    # Individual columns are the source of truth; restore them on top of the
    # stored cookie jar so old rows keep working after a migration.
    for key, value in (
        ("SESSDATA", row.sessdata),
        ("bili_jct", row.bili_jct),
        ("buvid3", row.buvid3),
        ("buvid4", row.buvid4),
        ("DedeUserID", row.dedeuserid),
        ("ac_time_value", row.ac_time_value),
    ):
        if value:
            cookie_map[key] = str(value)
    return Credential.from_cookies(cookie_map)


async def _credential_or_raise(account_key: str = "") -> Any:
    row = await _load_row(account_key)
    if row is None or not row.sessdata:
        raise RuntimeError("请先在「助手 → B站」登录 B站账号")
    if not await _row_is_valid(row):
        raise RuntimeError(
            "B站登录已失效（-101 账号未登录），"
            "请在「助手 → B站」重新扫码登录"
        )
    return _credential(row)


async def status(account_key: str = "", *, verify: bool = True) -> dict[str, Any]:
    row = await _load_row(account_key)
    if row is None or not row.sessdata:
        return _account_payload(None)
    if verify and not await _row_is_valid(row):
        payload = _account_payload(row)
        payload.update(
            {
                "logged_in": False,
                "expired": True,
                "message": "B站登录已失效，请重新扫码登录",
            }
        )
        return payload
    return _account_payload(row)


async def _save_credential(
    cred: Any,
    account_key: str = "",
    cookie_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    _require()
    if not str(getattr(cred, "sessdata", "") or ""):
        raise RuntimeError("B站登录未返回 SESSDATA，请重新登录")
    if not getattr(cred, "buvid3", None) or not getattr(cred, "buvid4", None):
        try:
            buvid_cookies = await cred.get_buvid_cookies()
            cred.buvid3 = (
                getattr(cred, "buvid3", None)
                or buvid_cookies.get("buvid3")
                or None
            )
            cred.buvid4 = (
                getattr(cred, "buvid4", None)
                or buvid_cookies.get("buvid4")
                or None
            )
        except Exception:
            pass
    stored_cookies = {
        str(key): str(value)
        for key, value in (cookie_map or {}).items()
        if value is not None and str(value) != ""
    }
    try:
        for key, value in (cred.get_cookies() or {}).items():
            # Credential.get_cookies() exposes both SESSDATA and the internal
            # sessdata attribute; keep only the canonical cookie names.
            if str(key) in {"sessdata", "dedeuserid", "proxy"}:
                continue
            if value is not None and str(value) != "":
                stored_cookies[str(key)] = str(value)
    except Exception:
        pass
    for key, value in (
        ("SESSDATA", getattr(cred, "sessdata", "")),
        ("bili_jct", getattr(cred, "bili_jct", "")),
        ("buvid3", getattr(cred, "buvid3", "")),
        ("buvid4", getattr(cred, "buvid4", "")),
        ("DedeUserID", getattr(cred, "dedeuserid", "")),
        ("ac_time_value", getattr(cred, "ac_time_value", "")),
    ):
        if value:
            stored_cookies[key] = str(value)
    name = ""
    mid = ""
    verified = False
    try:
        me = await user.get_self_info(cred)
        name = _clean(me.get("uname") or me.get("name"))
        mid = str(me.get("mid") or "")
        verified = True
    except Exception:
        pass
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(BiliAccount)
                .where(BiliAccount.account_key == account_key)
                .order_by(BiliAccount.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            row = BiliAccount(account_key=account_key)
            db.add(row)
        row.sessdata = str(getattr(cred, "sessdata", "") or "")
        row.bili_jct = str(getattr(cred, "bili_jct", "") or "")
        row.buvid3 = str(getattr(cred, "buvid3", "") or "")
        row.buvid4 = str(getattr(cred, "buvid4", "") or "")
        row.dedeuserid = str(getattr(cred, "dedeuserid", "") or "")
        row.ac_time_value = str(getattr(cred, "ac_time_value", "") or "")
        row.cookie_json = (
            json.dumps(stored_cookies, ensure_ascii=False, separators=(",", ":"))
            if stored_cookies
            else ""
        )
        row.name = name or row.name
        row.mid = mid or row.mid
        row.enabled = True
        row.updated_at = utcnow()
        await db.commit()
        await db.refresh(row)
    _VALID_CACHE[_cache_key(account_key or row.account_key)] = (
        time.monotonic(),
        bool(verified),
    )
    payload = _account_payload(row)
    payload["verified"] = verified
    return payload


async def clear_account(account_key: str = "") -> None:
    _VALID_CACHE.pop(_cache_key(account_key), None)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(BiliAccount).where(BiliAccount.account_key == account_key)
            )
        ).scalars().all()
        for row in rows:
            _VALID_CACHE.pop(_cache_key(row.account_key), None)
            await db.delete(row)
        await db.commit()


async def save_cookie(
    *,
    sessdata: str,
    bili_jct: str,
    buvid3: str = "",
    buvid4: str = "",
    dedeuserid: str = "",
    ac_time_value: str = "",
    account_key: str = "",
) -> dict[str, Any]:
    _require()
    sessdata = str(sessdata or "").strip()
    bili_jct = str(bili_jct or "").strip()
    if not sessdata or not bili_jct:
        raise ValueError("SESSDATA 和 bili_jct 不能为空")
    cred = Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3 or None,
        buvid4=buvid4 or None,
        dedeuserid=dedeuserid or None,
        ac_time_value=ac_time_value or None,
    )
    payload = await _save_credential(
        cred,
        account_key,
        {
            "SESSDATA": sessdata,
            "bili_jct": bili_jct,
            "buvid3": buvid3,
            "buvid4": buvid4,
            "DedeUserID": dedeuserid,
            "ac_time_value": ac_time_value,
        },
    )
    if not payload.get("verified"):
        await clear_account(account_key)
        raise RuntimeError("B站 Cookie 校验失败，请重新复制 SESSDATA / bili_jct")
    return payload


def _cleanup_sessions() -> None:
    now = time.time()
    for mapping in (QR_SESSIONS, PASSWORD_SESSIONS):
        for key, session in list(mapping.items()):
            if now - float(session.get("created") or 0) > SESSION_TTL:
                path = session.get("path")
                if path is not None:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass
                geetest = session.get("geetest")
                if geetest is not None:
                    try:
                        geetest.close_geetest_server()
                    except Exception:
                        pass
                mapping.pop(key, None)


def _credential_from_login_url(url: str, refresh_token: str = "") -> Any:
    query = parse_qs(urlsplit(str(url or "")).query)
    lower = {key.lower(): values for key, values in query.items()}
    def pick(name: str) -> str:
        values = query.get(name) or lower.get(name.lower()) or []
        return str(values[0]) if values else ""
    sessdata = pick("SESSDATA")
    bili_jct = pick("bili_jct")
    dedeuserid = pick("DedeUserID")
    return Credential(
        sessdata=sessdata or None,
        bili_jct=bili_jct or None,
        dedeuserid=dedeuserid or None,
        ac_time_value=refresh_token or None,
    )


async def start_qr_login() -> dict[str, Any]:
    _require()
    _cleanup_sessions()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com/",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            params={"source": "main-fe-header"},
            headers=headers,
        )
        payload = response.json()
        generate_cookies = dict(client.cookies)
    if int(payload.get("code") or 0) != 0:
        raise RuntimeError(f"B站二维码生成失败：{payload.get('message') or payload.get('code')}")
    data = payload.get("data") or {}
    qr_link = str(data.get("url") or "")
    qr_key = str(data.get("qrcode_key") or "")
    if not qr_link or not qr_key:
        raise RuntimeError("B站二维码接口没有返回 url/qrcode_key")
    key = secrets.token_hex(8)
    QR_DIR.mkdir(parents=True, exist_ok=True)
    path = QR_DIR / f"bili_qr_{key}.png"
    qrcode.make(qr_link).save(path)
    QR_SESSIONS[key] = {
        "qr_key": qr_key,
        "created": time.time(),
        "path": path,
        "cookies": generate_cookies,
    }
    return {
        "key": key,
        "status": "scan",
        "url": f"/media/assistant/{path.name}",
    }


async def poll_qr_login(key: str, account_key: str = "") -> dict[str, Any]:
    _require()
    _cleanup_sessions()
    session = QR_SESSIONS.get(key)
    if session is None:
        return {"status": "expired", "message": "二维码已过期，请重新生成"}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com/",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={
                "qrcode_key": session.get("qr_key") or "",
                "source": "main-fe-header",
            },
            headers=headers,
            cookies=session.get("cookies") or None,
        )
        payload = response.json()
        cookies = client.cookies
    if int(payload.get("code") or 0) != 0:
        return {
            "status": "error",
            "message": f"B站登录状态查询失败：{payload.get('message') or payload.get('code')}",
        }
    data = payload.get("data") or {}
    code = int(data.get("code") or 0)
    if code == 86101:
        return {"status": "scan"}
    if code == 86090:
        return {"status": "confirm"}
    if code == 86038:
        session = QR_SESSIONS.pop(key, None)
        if session and session.get("path"):
            try:
                Path(session["path"]).unlink(missing_ok=True)
            except Exception:
                pass
        return {"status": "timeout", "message": "二维码已过期，请重新生成"}
    cred = _credential_from_login_url(
        str(data.get("url") or ""),
        str(data.get("refresh_token") or ""),
    )
    # Some responses put cookies in Set-Cookie or cookie_info instead of the
    # cross-domain URL. Merge every source, including buvid3/buvid4 which are
    # required by several write APIs (like/comment/favorite).
    cookie_map: dict[str, str] = {}
    for source in (session.get("cookies") or {}, dict(cookies)):
        for name, value in source.items():
            if value:
                cookie_map[str(name)] = str(value)
    for item in ((data.get("cookie_info") or {}).get("cookies") or []):
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if name and value:
            cookie_map[name] = value
    cookie_cred = Credential.from_cookies(cookie_map)
    for field in ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid"):
        if not getattr(cred, field, None):
            setattr(cred, field, getattr(cookie_cred, field, None))
    if not cred.dedeuserid:
        for key in ("DedeUserID", "DedeUserID__"):
            if cookie_map.get(key):
                cred.dedeuserid = cookie_map[key]
                break
    if not cred.sessdata:
        logger.warning(
            "B站二维码登录成功但缺少 SESSDATA（cookie_info=%s）",
            bool(data.get("cookie_info")),
        )
        return {
            "status": "error",
            "message": "B站返回登录成功，但没有拿到 SESSDATA；请重新扫码",
        }
    try:
        account = await _save_credential(cred, account_key, cookie_map)
    except Exception as exc:
        return {"status": "error", "message": f"保存 B站登录失败：{exc}"}
    if not account.get("verified"):
        logger.warning("B站二维码登录凭据校验失败")
        await clear_account(account_key)
        return {
            "status": "error",
            "message": "B站 Cookie 校验失败，请重新扫码；如果多次失败请改用 Cookie 登录",
        }
    logger.info("B站二维码登录成功：sessdata_len=%s bili_jct=%s", len(cred.sessdata or ""), bool(cred.bili_jct))
    session = QR_SESSIONS.pop(key, None)
    if session and session.get("path"):
        try:
            Path(session["path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return {"status": "done", "account": account}


async def start_password_login(username: str, password: str) -> dict[str, Any]:
    _require()
    _cleanup_sessions()
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise ValueError("账号和密码不能为空")
    session_id = secrets.token_hex(8)
    geetest = Geetest()
    await geetest.generate_test(GeetestType.LOGIN)
    geetest.start_geetest_server()
    captcha_url = geetest.get_geetest_server_url()
    PASSWORD_SESSIONS[session_id] = {
        "geetest": geetest,
        "username": username,
        "password": password,
        "created": time.time(),
        "captcha_url": captcha_url,
    }
    return {
        "session_id": session_id,
        "status": "captcha",
        "captcha_url": captcha_url,
        "message": "请在新窗口完成 B站极验验证，然后回到这里点击「完成登录」",
    }


async def poll_password_login(
    session_id: str, account_key: str = ""
) -> dict[str, Any]:
    _require()
    _cleanup_sessions()
    session = PASSWORD_SESSIONS.get(session_id)
    if session is None:
        return {"status": "expired", "message": "登录会话已过期，请重新开始"}
    geetest = session["geetest"]
    if not geetest.has_done():
        return {
            "status": "captcha",
            "captcha_url": session.get("captcha_url"),
            "message": "等待完成极验验证",
        }
    try:
        result = await login_v2.login_with_password(
            session["username"], session["password"], geetest
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    if isinstance(result, login_v2.LoginCheck):
        PASSWORD_SESSIONS.pop(session_id, None)
        try:
            geetest.close_geetest_server()
        except Exception:
            pass
        return {
            "status": "verify",
            "message": "B站要求手机验证，请改用扫码登录或 Cookie 登录",
        }
    account = await _save_credential(result, account_key)
    PASSWORD_SESSIONS.pop(session_id, None)
    try:
        geetest.close_geetest_server()
    except Exception:
        pass
    if not account.get("verified"):
        await clear_account(account_key)
        return {
            "status": "error",
            "message": "B站登录校验失败，请改用扫码登录或 Cookie 登录",
        }
    return {"status": "done", "account": account}


async def search_videos(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    _require()
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("搜索关键词不能为空")
    result = await search.search_by_type(
        keyword,
        search_type=search.SearchObjectType.VIDEO,
        page=1,
        page_size=max(1, min(30, int(limit))),
    )
    return [_video_item(item) for item in (result.get("result") or [])]


async def recommend_videos(limit: int = 8) -> list[dict[str, Any]]:
    _require()
    result = await hot.get_hot_videos(pn=1, ps=max(1, min(30, int(limit))))
    return [_video_item(item) for item in (result.get("list") or [])]


async def video_info(bvid: str) -> dict[str, Any]:
    _require()
    bvid = str(bvid or "").strip()
    if not bvid:
        raise ValueError("bvid 不能为空")
    row = await _load_row()
    cred = _credential(row) if row and row.sessdata else None
    v = video.Video(bvid=bvid, credential=cred)
    return _normalize_info(await v.get_info())


async def watch_video(
    bvid: str, *, open_browser: bool = True, account_key: str = ""
) -> dict[str, Any]:
    _require()
    bvid = str(bvid or "").strip()
    if not bvid:
        raise ValueError("bvid 不能为空")
    row = await _load_row(account_key)
    cred = _credential(row) if row and row.sessdata else None
    v = video.Video(bvid=bvid, credential=cred)
    info = _normalize_info(await v.get_info())
    reported = False
    if cred is not None:
        try:
            await v.report_start_watching()
            reported = True
        except Exception:
            pass
    opened = False
    if open_browser and info.get("url"):
        try:
            opened = bool(webbrowser.open(info["url"]))
        except Exception:
            opened = False
    return {"video": info, "opened": opened, "watch_reported": reported}


async def like_video(
    bvid: str, status_value: bool = True, account_key: str = ""
) -> dict[str, Any]:
    _require()
    cred = await _credential_or_raise(account_key)
    v = video.Video(bvid=str(bvid or "").strip(), credential=cred)
    result = await _run_authed(
        v.like(bool(status_value)), account_key=account_key, action="点赞"
    )
    return {
        "bvid": bvid,
        "liked": bool(status_value),
        "result": result,
    }


async def comment_video(
    bvid: str, text: str, account_key: str = ""
) -> dict[str, Any]:
    _require()
    text = str(text or "").strip()
    if not text:
        raise ValueError("评论内容不能为空")
    if len(text) > 1000:
        raise ValueError("评论不能超过 1000 字")
    cred = await _credential_or_raise(account_key)
    v = video.Video(bvid=str(bvid or "").strip(), credential=cred)
    aid = v.get_aid()
    result = await _run_authed(
        comment.send_comment(
            text,
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            credential=cred,
        ),
        account_key=account_key,
        action="评论",
    )
    return {"bvid": bvid, "commented": True, "result": result}


async def feed_videos(
    limit: int = 5, *, open_browser: bool = True, account_key: str = ""
) -> dict[str, Any]:
    videos = await recommend_videos(limit)
    opened = None
    if open_browser and videos:
        opened = await watch_video(
            videos[0]["bvid"], open_browser=True, account_key=account_key
        )
    return {"videos": videos, "opened": opened}


async def _download_audio_url(bvid: str) -> str:
    _require()
    v = video.Video(bvid=bvid)
    data = await v.get_download_url(page_index=0)
    audios = (data.get("dash") or {}).get("audio") or []
    if not audios:
        raise RuntimeError("这个视频没有可下载的音频流")
    audios.sort(key=lambda item: item.get("bandwidth") or 0, reverse=True)
    url = audios[0].get("baseUrl") or audios[0].get("base_url")
    if not url:
        raise RuntimeError("没有拿到音频下载地址")
    return str(url)


async def download_audio(bvid: str) -> Path:
    url = await _download_audio_url(bvid)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    target = AUDIO_DIR / f"{bvid}.m4s"
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            },
        )
        response.raise_for_status()
        target.write_bytes(response.content)
    if target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        raise RuntimeError("音频文件过小，下载可能失败")
    return target


def _load_asr_model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def _transcribe_file(model, path: Path) -> str:
    segments, _info = model.transcribe(
        str(path),
        language="zh",
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
        initial_prompt="以下是普通话视频的语音内容。",
    )
    return "".join(segment.text for segment in segments).strip()


async def _subtitle_text(bvid: str, account_key: str = "") -> str:
    row = await _load_row(account_key)
    if row is None or not row.sessdata:
        return ""
    cred = _credential(row)
    v = video.Video(bvid=bvid, credential=cred)
    info = await v.get_info()
    cid = info.get("cid")
    try:
        data = await v.get_subtitle(cid)
    except Exception:
        data = {}
    entries = data.get("subtitles") if isinstance(data, dict) else []
    for entry in entries or []:
        url = str((entry or {}).get("subtitle_url") or "")
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            body = payload.get("body") or []
            text = "".join(str(item.get("content") or "") for item in body)
            if text.strip():
                return text.strip()
        except Exception:
            continue
    try:
        conclusion = await v.get_ai_conclusion(cid)
        summary = conclusion.get("summary") if isinstance(conclusion, dict) else ""
        if summary:
            return str(summary)
    except Exception:
        pass
    return ""


async def transcribe_video(
    bvid: str, model_size: str = "small", account_key: str = ""
) -> dict[str, Any]:
    bvid = str(bvid or "").strip()
    if not bvid:
        raise ValueError("bvid 不能为空")
    subtitle = await _subtitle_text(bvid, account_key)
    if len(subtitle) >= 30:
        return {"bvid": bvid, "text": subtitle, "source": "subtitle"}
    path = await download_audio(bvid)
    try:
        async with _ASR_LOCK:
            model = _ASR_MODELS.get(model_size)
            if model is None:
                model = await asyncio.to_thread(_load_asr_model, model_size)
                _ASR_MODELS[model_size] = model
            text = await asyncio.to_thread(_transcribe_file, model, path)
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    if not text:
        raise RuntimeError("音频转写没有返回文本")
    return {"bvid": bvid, "text": text, "source": "asr", "model": model_size}


async def record_video_to_knowledge(
    bvid: str,
    model_size: str = "small",
    account_key: str = "",
) -> dict[str, Any]:
    """Transcribe a video on demand and add it to the local knowledge base."""
    bvid = str(bvid or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        raise ValueError("bvid 格式不正确")
    info = await video_info(bvid)
    transcribed = await transcribe_video(bvid, model_size, account_key)
    from app.services import bili_knowledge

    added = await bili_knowledge.add_video_async(
        bvid=str(bvid or "").strip(),
        title=str(info.get("title") or bvid),
        author=str(info.get("author") or ""),
        url=str(info.get("url") or ""),
        transcript=str(transcribed.get("text") or ""),
        summary="",
        topics=[],
    )
    return {
        "bvid": bvid,
        "title": info.get("title") or bvid,
        "recorded": True,
        "chunks": int(added.get("added") or 0),
        "transcript_source": transcribed.get("source") or "",
        "transcript_chars": len(str(transcribed.get("text") or "")),
    }


async def get_video_comments(
    bvid: str, limit: int = 10, account_key: str = ""
) -> list[dict[str, Any]]:
    _require()
    row = await _load_row(account_key)
    cred = None
    if row is not None and row.sessdata:
        try:
            if await _row_is_valid(row):
                cred = _credential(row)
        except Exception:
            cred = None
    v = video.Video(bvid=bvid, credential=cred)
    aid = v.get_aid()
    data = await comment.get_comments(
        aid,
        comment.CommentResourceType.VIDEO,
        page_index=1,
        order=comment.OrderType.LIKE,
        credential=cred,
    )
    replies = data.get("replies") or []
    result = []
    for item in replies[: max(1, int(limit))]:
        member = item.get("member") or {}
        content = item.get("content") or {}
        result.append(
            {
                "rpid": item.get("rpid"),
                "uname": member.get("uname") or "",
                "content": content.get("message") or "",
                "like": item.get("like") or 0,
                "reply_count": item.get("rcount") or 0,
            }
        )
    return result


async def reply_video_comment(
    bvid: str, text: str, root: int, parent: int | None = None,
    account_key: str = "",
) -> dict[str, Any]:
    _require()
    text = str(text or "").strip()
    if not text:
        raise ValueError("回复内容不能为空")
    cred = await _credential_or_raise(account_key)
    v = video.Video(bvid=bvid, credential=cred)
    aid = v.get_aid()
    result = await _run_authed(
        comment.send_comment(
            text,
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            root=int(root),
            parent=int(parent or root),
            credential=cred,
        ),
        account_key=account_key,
        action="回复评论",
    )
    return {"bvid": bvid, "replied": True, "result": result}


async def favorite_video(bvid: str, account_key: str = "") -> dict[str, Any]:
    _require()
    cred = await _credential_or_raise(account_key)
    me = await _run_authed(
        user.get_self_info(cred), account_key=account_key, action="收藏"
    )
    uid = int(me.get("mid") or 0)
    if not uid:
        raise RuntimeError("无法获取 B站 UID")
    folders = await _run_authed(
        favorite_list.get_video_favorite_list(uid, credential=cred),
        account_key=account_key,
        action="收藏",
    )
    items = folders.get("list") or []
    if not items:
        raise RuntimeError("没有可用的 B站收藏夹")
    folder_id = int(items[0].get("id") or 0)
    v = video.Video(bvid=bvid, credential=cred)
    result = await _run_authed(
        v.set_favorite(add_media_ids=[folder_id]),
        account_key=account_key,
        action="收藏",
    )
    return {"bvid": bvid, "favorited": True, "folder_id": folder_id, "result": result}
