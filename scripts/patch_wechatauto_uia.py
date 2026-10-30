"""Upgrade-safe patch for the installed ``wechatauto`` (wechatauto-replica).

WeChat 4.2.x moved the QAccessible "gate" RVA again, and driving a minimized or
narrow client window is what took the process down during the 4.2.0.12 upgrade.
This script makes two idempotent, text-anchored edits to the installed package:

1. add the verified 4.2.0.12 gate RVA to ``QACCESSIBLE_ACTIVE_RVA_BY_VERSION``
   so a cold scan failure still lands on a known-good address;
2. honour ``WECHATAUTO_NO_UIA_HOTWRITE=1`` — only write an RVA that has already
   verified on this exact DLL, never blind-write unverified candidates.

Run it after every ``pip install --upgrade wechatauto-replica``::

    python scripts/patch_wechatauto_uia.py --check
    python scripts/patch_wechatauto_uia.py

The package file is backed up next to itself (``accessibility.py.bak``).
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

GATE_RVA_ANCHOR = '    "4.1.15.53": 0x0B3959D8,   # 2026-10-17 实测：扫描得出，热写后校验通过\n'
GATE_RVA_PATCH = (
    GATE_RVA_ANCHOR
    + '    "4.2.0.12": 0x0B507C08,    # 2026-10-30 实测：4.2.x 升级后扫描+校验通过\n'
)

VERIFIED_ANCHOR = "        if verified is not None:\n            out.append(int(verified))\n"
VERIFIED_PATCH = (
    VERIFIED_ANCHOR
    + "        if os.environ.get('WECHATAUTO_NO_UIA_HOTWRITE'):\n"
    + "            # 只写「在本机这份 DLL 上校验通过」的地址：版本一升级 RVA 就会漂移，\n"
    + "            # 盲写候选等同于往客户端内存里乱改字节，写错就崩。宁可这轮退回\n"
    + "            # OCR/坐标，也不要为了快一点把微信写崩。\n"
    + "            return out\n"
)


def locate_accessibility() -> Path:
    spec = importlib.util.find_spec("wechatauto")
    if spec is None or not spec.origin:
        raise SystemExit("wechatauto 未安装，先 pip install wechatauto-replica")
    pkg_dir = Path(spec.origin).parent
    target = pkg_dir / "accessibility.py"
    if not target.is_file():
        raise SystemExit(f"找不到 {target}")
    return target


def apply(path: Path, text: str, anchor: str, patch: str, label: str) -> tuple[str, bool]:
    if patch in text:
        print(f"[skip] {label}: 已经是补丁后的状态")
        return text, False
    if anchor not in text:
        print(f"[warn] {label}: 找不到锚点，跳过（上游可能已改结构，请人工确认）")
        return text, False
    print(f"[ok] {label}: 已打补丁")
    return text.replace(anchor, patch, 1), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = parser.parse_args()

    path = locate_accessibility()
    original = path.read_text(encoding="utf-8")

    text = original
    text, a = apply(path, text, GATE_RVA_ANCHOR, GATE_RVA_PATCH, "4.2.0.12 gate RVA")
    text, b = apply(path, text, VERIFIED_ANCHOR, VERIFIED_PATCH, "WECHATAUTO_NO_UIA_HOTWRITE 开关")

    if args.check:
        print("[check] 未写入文件")
        return 0 if not (a or b) else 0

    if text == original:
        print("无需改动")
        return 0

    backup = path.with_suffix(".py.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"[backup] {backup}")
    path.write_text(text, encoding="utf-8")
    print(f"[done] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
