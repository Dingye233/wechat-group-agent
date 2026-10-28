"""Check, install or update the DeepSeek Harness runtime.

    .\\.venv312\\Scripts\\python.exe scripts\\setup_dsh.py --check
    .\\.venv312\\Scripts\\python.exe scripts\\setup_dsh.py --channel stable
    .\\.venv312\\Scripts\\python.exe scripts\\setup_dsh.py --status

The version source is the GitHub repository's ``dsh-v*`` tags; the binary is the
matching official npm package, installed into ``data/dsh/runtimes/<version>``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.dsh_runtime import get_dsh_runtime  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="DeepSeek Harness runtime manager")
    parser.add_argument("--check", action="store_true", help="only check for updates")
    parser.add_argument("--status", action="store_true", help="print current status")
    parser.add_argument("--channel", choices=("stable", "alpha"), default="stable")
    args = parser.parse_args()
    runtime = get_dsh_runtime()
    if args.status:
        print(json.dumps(runtime.status(), ensure_ascii=False, indent=2))
        return 0
    if args.check:
        print(
            json.dumps(
                await runtime.check_update(args.channel),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = await runtime.update(args.channel)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(runtime.status(), ensure_ascii=False, indent=2))
    return 0 if runtime.installed_version() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
