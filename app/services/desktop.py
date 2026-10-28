from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

SCREEN_DIR = Path("data") / "media" / "assistant"


def _region(value: Any) -> tuple[int, int, int, int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parts = [int(item.strip()) for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        parts = [int(item) for item in value]
    else:
        raise ValueError("region 需要 x,y,w,h")
    if len(parts) != 4:
        raise ValueError("region 需要 x,y,w,h")
    x, y, width, height = parts
    return (x, y, x + width, y + height)


def _grab(region: Any = None):
    box = _region(region)
    return ImageGrab.grab(bbox=box, all_screens=True)


def _save(image, prefix: str = "screen") -> tuple[Path, str]:
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREEN_DIR / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time()*1000)%1000}.png"
    image.save(path, format="PNG")
    return path, f"/media/assistant/{path.name}"


def screen_size() -> dict[str, int]:
    image = _grab()
    return {"width": image.width, "height": image.height}


def capture_screen(region: Any = None) -> dict[str, Any]:
    image = _grab(region)
    path, url = _save(image)
    try:
        import pyautogui

        cursor = {"x": int(pyautogui.position().x), "y": int(pyautogui.position().y)}
    except Exception:
        cursor = {}
    return {
        "path": str(path),
        "url": url,
        "width": image.width,
        "height": image.height,
        "cursor": cursor,
    }


def ocr_screen(region: Any = None) -> dict[str, Any]:
    image = _grab(region)
    try:
        result = ocr_image(image, "auto")
    except Exception as exc:
        raise RuntimeError(f"屏幕 OCR 失败：{exc}") from exc
    lines = [
        item for item in (result.get("items") or []) if str(item.get("text") or "").strip()
    ]
    path, url = _save(image, "ocr")
    return {
        "path": str(path),
        "url": url,
        "width": image.width,
        "height": image.height,
        "text": result.get("text") or "",
        "items": lines[:300],
        "backend": result.get("backend"),
    }


def find_text_on_screen(text: str, region: Any = None) -> dict[str, Any]:
    target = str(text or "").strip().lower()
    if not target:
        raise ValueError("要查找的文字不能为空")
    result = ocr_screen(region)
    matches = [
        item
        for item in result.get("items", [])
        if target in str(item.get("text") or "").lower()
    ]
    return {"query": text, "matches": matches, "count": len(matches), "ocr": result}


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.06
    return pyautogui


def mouse_move(x: int, y: int, duration: float = 0.3) -> dict[str, Any]:
    gui = _pyautogui()
    gui.moveTo(int(x), int(y), duration=max(0.0, min(3.0, float(duration))), tween=gui.easeInOutQuad)
    return {"x": int(x), "y": int(y)}


def mouse_click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.12,
) -> dict[str, Any]:
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.moveTo(int(x), int(y), duration=0.18, tween=gui.easeInOutQuad)
    gui.click(
        x=None if x is None else int(x),
        y=None if y is None else int(y),
        clicks=max(1, min(3, int(clicks))),
        interval=max(0.05, float(interval)),
        button=button if button in ("left", "right", "middle") else "left",
    )
    pos = gui.position()
    return {"x": int(pos.x), "y": int(pos.y), "button": button, "clicks": clicks}


def mouse_drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration: float = 0.5,
    button: str = "left",
) -> dict[str, Any]:
    gui = _pyautogui()
    gui.moveTo(int(x1), int(y1), duration=0.2)
    gui.dragTo(
        int(x2),
        int(y2),
        duration=max(0.1, min(5.0, float(duration))),
        button=button if button in ("left", "right", "middle") else "left",
    )
    return {"from": [int(x1), int(y1)], "to": [int(x2), int(y2)]}


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.moveTo(int(x), int(y), duration=0.15)
    gui.scroll(int(amount))
    return {"amount": int(amount)}


def keyboard_type(text: str, interval: float = 0.02) -> dict[str, Any]:
    value = str(text or "")
    if not value:
        raise ValueError("输入内容不能为空")
    gui = _pyautogui()
    if value.isascii():
        gui.write(value, interval=max(0.0, min(0.2, float(interval))))
    else:
        import pyperclip

        old = ""
        try:
            old = pyperclip.paste()
        except Exception:
            pass
        try:
            pyperclip.copy(value)
            gui.hotkey("ctrl", "v")
            time.sleep(0.2)
        finally:
            try:
                if old:
                    pyperclip.copy(old)
            except Exception:
                pass
    return {"text": value, "length": len(value)}


def keyboard_press(key: str, presses: int = 1) -> dict[str, Any]:
    gui = _pyautogui()
    gui.press(str(key), presses=max(1, min(20, int(presses))), interval=0.08)
    return {"key": key, "presses": presses}


def keyboard_hotkey(keys: list[str] | str) -> dict[str, Any]:
    if isinstance(keys, str):
        parts = [item.strip() for item in keys.split("+") if item.strip()]
    else:
        parts = [str(item).strip() for item in keys if str(item).strip()]
    if not parts:
        raise ValueError("快捷键不能为空")
    gui = _pyautogui()
    gui.hotkey(*parts)
    return {"keys": parts}


def execute(name: str, args: dict[str, Any] | None = None) -> Any:
    args = args or {}
    if name == "screen_capture":
        return capture_screen(args.get("region"))
    if name == "screen_ocr":
        return ocr_screen(args.get("region"))
    if name == "screen_find_text":
        return find_text_on_screen(str(args.get("text") or ""), args.get("region"))
    if name == "mouse_move":
        return mouse_move(int(args["x"]), int(args["y"]), float(args.get("duration") or 0.3))
    if name == "mouse_click":
        return mouse_click(
            args.get("x"), args.get("y"), str(args.get("button") or "left"),
            int(args.get("clicks") or 1), float(args.get("interval") or 0.12),
        )
    if name == "mouse_drag":
        return mouse_drag(
            int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"]),
            float(args.get("duration") or 0.5), str(args.get("button") or "left"),
        )
    if name == "mouse_scroll":
        return mouse_scroll(int(args.get("amount") or -300), args.get("x"), args.get("y"))
    if name == "keyboard_type":
        return keyboard_type(str(args.get("text") or ""), float(args.get("interval") or 0.02))
    if name == "keyboard_press":
        return keyboard_press(str(args.get("key") or ""), int(args.get("presses") or 1))
    if name == "keyboard_hotkey":
        return keyboard_hotkey(args.get("keys") or [])
    raise ValueError(f"未知桌面操作：{name}")


def _windows_ocr(image) -> dict[str, Any]:
    from wechatauto.guia import ScreenOCR

    items = ScreenOCR.recognize(image)
    lines = []
    for item in items:
        if len(item) >= 5:
            text, x, y, width, height = item[:5]
        else:
            text, x, y = item[0], item[1], item[2]
            width = height = 0
        if str(text or "").strip():
            lines.append(
                {
                    "text": str(text),
                    "x": int(x),
                    "y": int(y),
                    "width": int(width),
                    "height": int(height),
                }
            )
    return {
        "text": "\n".join(item["text"] for item in lines),
        "items": lines,
        "backend": "windows",
    }


_RAPID_ENGINE = None


def _rapid_ocr(image) -> dict[str, Any] | None:
    """Fast offline OCR via RapidOCR (PP-OCR models + ONNX Runtime)."""
    global _RAPID_ENGINE
    try:
        from rapidocr import RapidOCR  # type: ignore
    except Exception:
        return None
    try:
        if _RAPID_ENGINE is None:
            _RAPID_ENGINE = RapidOCR()
        import numpy as np

        array = np.asarray(image.convert("RGB"))[:, :, ::-1]
        raw = _RAPID_ENGINE(array)
        lines: list[dict[str, Any]] = []
        if hasattr(raw, "txts"):
            texts = list(getattr(raw, "txts", None) or [])
            boxes = getattr(raw, "boxes", None)
            scores = list(getattr(raw, "scores", None) or [])
            for index, text in enumerate(texts):
                if not str(text or "").strip():
                    continue
                item: dict[str, Any] = {"text": str(text)}
                if boxes is not None and index < len(boxes):
                    box = boxes[index]
                    xs = [float(point[0]) for point in box]
                    ys = [float(point[1]) for point in box]
                    item.update(
                        {
                            "x": int(min(xs)),
                            "y": int(min(ys)),
                            "width": int(max(xs) - min(xs)),
                            "height": int(max(ys) - min(ys)),
                        }
                    )
                else:
                    item.update({"x": 0, "y": 0, "width": 0, "height": 0})
                if index < len(scores):
                    item["score"] = float(scores[index])
                lines.append(item)
        else:
            result = raw[0] if isinstance(raw, tuple) else raw
            for row in result or []:
                box, text, score = row[0], row[1], row[2]
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                lines.append(
                    {
                        "text": str(text),
                        "x": int(min(xs)),
                        "y": int(min(ys)),
                        "width": int(max(xs) - min(xs)),
                        "height": int(max(ys) - min(ys)),
                        "score": float(score),
                    }
                )
        return {
            "text": "\n".join(item["text"] for item in lines),
            "items": lines,
            "backend": "rapidocr",
        }
    except Exception:
        return None


def _paddle_ocr(image) -> dict[str, Any] | None:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return None
    try:
        engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = engine.ocr(image, cls=True)
        lines = []
        for page in result or []:
            for row in page or []:
                box, (text, score) = row[0], row[1]
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                lines.append(
                    {
                        "text": str(text),
                        "x": int(min(xs)),
                        "y": int(min(ys)),
                        "width": int(max(xs) - min(xs)),
                        "height": int(max(ys) - min(ys)),
                        "score": float(score),
                    }
                )
        return {
            "text": "\n".join(item["text"] for item in lines),
            "items": lines,
            "backend": "paddleocr",
        }
    except Exception:
        return None


def ocr_image(image_or_path, backend: str = "auto") -> dict[str, Any]:
    from PIL import Image

    image = (
        Image.open(image_or_path).convert("RGB")
        if isinstance(image_or_path, (str, Path))
        else image_or_path
    )
    selected = str(backend or "auto").lower()
    if selected in ("auto", "rapid", "rapidocr"):
        result = _rapid_ocr(image)
        if result is not None:
            return result
    if selected == "rapid":
        raise RuntimeError(
            "RapidOCR 未安装或初始化失败；可 pip install rapidocr onnxruntime"
        )
    if selected in ("auto", "paddle", "paddleocr"):
        result = _paddle_ocr(image)
        if result is not None:
            return result
    if selected == "paddle":
        raise RuntimeError("PaddleOCR 未安装；可 pip install paddleocr paddlepaddle")
    return _windows_ocr(image)
