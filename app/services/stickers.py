from __future__ import annotations

import io
import random
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Sticker
from app.services.serializers import sticker_url

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

SAMPLE_DEFS = [
    {
        "name": "收到",
        "slug": "ok",
        "text": "收到",
        "sub": "ROGER THAT",
        "colors": ("#0e7a4b", "#20c97c"),
        "tags": ["收到", "OK", "好的", "同意"],
        "description": "认真点头，表示收到",
    },
    {
        "name": "笑死",
        "slug": "laugh",
        "text": "笑死",
        "sub": "LOL",
        "colors": ("#a85b00", "#ffc43d"),
        "tags": ["笑死", "哈哈", "开心", "搞笑"],
        "description": "笑到停不下来",
    },
    {
        "name": "点赞",
        "slug": "like",
        "text": "点赞",
        "sub": "NICE",
        "colors": ("#1549a8", "#4b9dff"),
        "tags": ["点赞", "赞", "支持", "牛"],
        "description": "竖起大拇指，表示支持",
    },
    {
        "name": "摸鱼",
        "slug": "fish",
        "text": "摸鱼",
        "sub": "TAKE A BREAK",
        "colors": ("#5633a8", "#b07bff"),
        "tags": ["摸鱼", "累了", "休息", "下班"],
        "description": "今天先摸一会儿",
    },
    {
        "name": "晚安",
        "slug": "night",
        "text": "晚安",
        "sub": "GOOD NIGHT",
        "colors": ("#14213d", "#3c5a99"),
        "tags": ["晚安", "睡觉", "拜拜", "下班"],
        "description": "关灯睡觉，明天见",
    },
    {
        "name": "问号",
        "slug": "what",
        "text": "？？？",
        "sub": "WHAT?",
        "colors": ("#9c1f4a", "#ff6b9d"),
        "tags": ["问号", "疑惑", "什么", "离谱"],
        "description": "满头问号，表示疑惑",
    },
]


async def list_stickers(db: AsyncSession, tag: str = "") -> list[Sticker]:
    rows = (await db.execute(select(Sticker).order_by(Sticker.id.desc()))).scalars().all()
    if not tag:
        return list(rows)
    needle = tag.strip().lower()
    return [
        row
        for row in rows
        if needle in (row.name or "").lower()
        or needle in (row.description or "").lower()
        or any(needle == str(t).lower() or needle in str(t).lower() for t in (row.tags or []))
    ]


async def all_tags(db: AsyncSession) -> list[str]:
    rows = (await db.execute(select(Sticker.tags))).scalars().all()
    tags: set[str] = set()
    for item in rows:
        for tag in item or []:
            if str(tag).strip():
                tags.add(str(tag).strip())
    return sorted(tags)


async def sticker_path(sticker: Sticker) -> Path:
    return settings.sticker_dir / sticker.filename


def _font_paths() -> list[str]:
    return [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _font_paths():
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except Exception:
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
    return ImageFont.load_default()


def _gradient(size: int, top: str, bottom: str) -> Image.Image:
    image = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(image)
    start = _hex_color(top)
    end = _hex_color(bottom)
    for y in range(size):
        ratio = y / max(size - 1, 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return image


def _hex_color(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _draw_emoji_faces(image: Image.Image, seed: int) -> None:
    size = image.width
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rng = random.Random(seed)
    for _ in range(7):
        radius = rng.randint(size // 18, size // 9)
        x = rng.randint(-radius, size - radius)
        y = rng.randint(-radius, size - radius)
        draw.ellipse(
            (x, y, x + radius * 2, y + radius * 2),
            fill=(255, 255, 255, rng.randint(14, 34)),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=2))
    image.paste(overlay, (0, 0), overlay)


def _make_sample(path: Path, definition: dict[str, Any]) -> None:
    size = 512
    image = _gradient(size, *definition["colors"]).convert("RGBA")
    _draw_emoji_faces(image, seed=sum(ord(c) for c in definition["slug"]))

    # White sticker border + inner rounded panel.
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (24, 24, size - 24, size - 24),
        radius=54,
        outline=(255, 255, 255, 235),
        width=10,
    )
    text = definition["text"]
    font_size = 150 if len(text) <= 2 else 122
    font = _load_font(font_size)
    sub_font = _load_font(34)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2
    y = (size - text_h) / 2 - 26
    draw.text((x + 6, y + 8), text, font=font, fill=(0, 0, 0, 60))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    sub = definition["sub"]
    sub_bbox = draw.textbbox((0, 0), sub, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.rounded_rectangle(
        ((size - sub_w) / 2 - 22, size - 120, (size + sub_w) / 2 + 22, size - 58),
        radius=30,
        fill=(0, 0, 0, 42),
    )
    draw.text(
        ((size - sub_w) / 2, size - 112),
        sub,
        font=sub_font,
        fill=(255, 255, 255, 230),
    )
    image.convert("RGB").save(path, format="PNG", optimize=True)


async def seed_sample_stickers(db: AsyncSession) -> int:
    count = (await db.execute(select(func.count(Sticker.id)))).scalar() or 0
    if count:
        return 0
    created = 0
    for definition in SAMPLE_DEFS:
        filename = f"builtin-{definition['slug']}.png"
        path = settings.sticker_dir / filename
        if not path.exists():
            _make_sample(path, definition)
        image = Image.open(path)
        db.add(
            Sticker(
                name=definition["name"],
                tags=definition["tags"],
                description=definition["description"],
                filename=filename,
                mime="image/png",
                width=image.width,
                height=image.height,
                size_bytes=path.stat().st_size,
            )
        )
        created += 1
    await db.commit()
    return created


async def create_from_upload(
    db: AsyncSession,
    *,
    data: bytes,
    original_name: str,
    name: str,
    tags: list[str],
    description: str,
) -> Sticker:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("表情包不能超过 5MB")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_MIME:
        raise ValueError("仅支持 PNG / JPG / GIF / WEBP 格式")
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        probe = Image.open(io.BytesIO(data))
        width, height = probe.size
        if width < 8 or height < 8:
            raise ValueError("图片尺寸过小")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法解析图片，请更换文件") from exc

    # WEBP is not reliably supported by WeChat's image pipeline.
    if suffix == ".webp":
        converted = probe.convert("RGB")
        buffer = io.BytesIO()
        converted.save(buffer, format="PNG")
        data = buffer.getvalue()
        suffix = ".png"
        width, height = converted.size

    mime = ALLOWED_MIME[suffix]
    filename = f"{uuid.uuid4().hex}{suffix}"
    path = settings.sticker_dir / filename
    path.write_bytes(data)

    sticker = Sticker(
        name=(name or Path(original_name).stem)[:120],
        tags=[tag[:40] for tag in tags if tag.strip()][:20],
        description=description[:400],
        filename=filename,
        mime=mime,
        width=width,
        height=height,
        size_bytes=len(data),
    )
    db.add(sticker)
    await db.commit()
    await db.refresh(sticker)
    return sticker


async def delete_sticker(db: AsyncSession, sticker: Sticker) -> None:
    filename = sticker.filename
    await db.delete(sticker)
    await db.commit()
    path = settings.sticker_dir / filename
    if path.exists() and not filename.startswith("builtin-"):
        try:
            path.unlink()
        except OSError:
            pass


async def resolve_sticker(db: AsyncSession, value: Any) -> Sticker | None:
    if value is None or value == "":
        return None
    rows = (await db.execute(select(Sticker))).scalars().all()
    if not rows:
        return None
    raw = str(value).strip()
    lowered = raw.lower()
    if lowered in {"random", "any", "随机", "任意"}:
        return random.choice(rows)
    if raw.isdigit():
        for row in rows:
            if row.id == int(raw):
                return row
    for row in rows:
        if row.name and row.name.lower() == lowered:
            return row
    for row in rows:
        if any(str(tag).lower() == lowered for tag in (row.tags or [])):
            return row
    for row in rows:
        if lowered in (row.name or "").lower():
            return row
    for row in rows:
        if any(lowered in str(tag).lower() for tag in (row.tags or [])):
            return row
    for row in rows:
        if lowered and lowered in (row.description or "").lower():
            return row
    return None


def sticker_prompt_catalog(stickers: list[Sticker]) -> str:
    if not stickers:
        return "（当前没有可用表情包）"
    lines = []
    for row in stickers[:40]:
        tags = "、".join(str(tag) for tag in (row.tags or []))
        desc = row.description or row.name
        lines.append(f"- id={row.id} 名称={row.name} 标签={tags or '无'} 描述={desc}")
    return "\n".join(lines)


def sample_sticker_urls() -> list[str]:
    return [sticker_url(f"builtin-{item['slug']}.png") for item in SAMPLE_DEFS]
