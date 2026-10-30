#!/usr/bin/env python3
"""群伴 · 首次启动自举（bootstrap）

``git clone`` 下来的仓库只有源码：``.venv*`` / ``.env`` / ``data/`` / ``node_modules``
都在 .gitignore 里，所以「装完不能直接用」不是操作问题，而是缺一层引导。
这个脚本把缺的东西一次补齐，并且可以反复运行（幂等）：

  1. 检查 Python 版本 / 位数（Windows 需要 64 位 3.11+）
  2. 选或建虚拟环境（.venv312 优先，其次 .venv）
  3. 安装 requirements.txt（内容变了才重装）
  4. 按需安装功能模块依赖：replica / ocr / bilibili / memory / uia / wcf
  5. 从 .env.example 生成 .env（UTF-8），补齐缺失键
  6. 建 data/ 目录骨架（media、stickers、knowledge、dsh…）
  7. 初始化 data/agent.db（建表 + 就地迁移）
  8. 检查 DSH 运行时与微信客户端，缺了给出下一步命令

用法：
    python scripts/bootstrap.py                # 补齐缺失项（推荐）
    python scripts/bootstrap.py --check        # 只体检，不改动
    python scripts/bootstrap.py --force        # 忽略缓存，重装依赖
    python scripts/bootstrap.py --extras all   # 装全部功能模块
    python scripts/bootstrap.py --with-dsh     # 顺带装 DSH 运行时（约 700MB）
    python scripts/bootstrap.py --mirror tsinghua   # 用国内 PyPI 镜像

退出码：0 = 可用（可能有警告）；1 = 关键步骤失败，起不来。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / ".bootstrap-state.json"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 11)
IS_WINDOWS = os.name == "nt"

MIRRORS = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple",
    "ustc": "https://mirrors.ustc.edu.cn/pypi/simple",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
}

# 能力模块：key -> (requirements 文件, 说明)
EXTRAS: dict[str, tuple[str, str]] = {
    "replica": ("requirements-replica.txt", "微信 4.x 收发（wechatauto-replica）"),
    "ocr": ("requirements-ocr.txt", "离线高精度 OCR（RapidOCR）"),
    "bilibili": ("requirements-bilibili.txt", "B站巡航 + 语音转写 + 向量知识库"),
    "memory": ("requirements-memory.txt", "Mem0 语义记忆"),
    "uia": ("requirements-uia.txt", "微信 3.9 UIA 适配器（需 32 位 Python）"),
    "wcf": ("requirements-wcf.txt", "微信 3.9 协议 WeChatFerry"),
}

# 用来判断模块是否真的装好了（缓存丢了也骗不过 import）
EXTRA_PROBES: dict[str, tuple[str, ...]] = {
    "replica": ("wechatauto",),
    "ocr": ("rapidocr", "onnxruntime"),
    "bilibili": ("bilibili_api", "faster_whisper"),
    "memory": ("mem0",),
    "uia": ("wxauto", "win32com"),
    "wcf": ("wcferry",),
}

DATA_DIRS = (
    "data",
    "data/stickers",
    "data/media",
    "data/media/chat",
    "data/media/replica",
    "data/media/assistant",
    "data/knowledge/chroma",
    "data/memory/mem0",
    "data/dsh/runtimes",
)

BASE_IMPORTS = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "aiosqlite",
    "pydantic",
    "pydantic_settings",
    "python_multipart",
    "httpx",
    "mcp",
    "PIL",
    "openpyxl",
    "qrcode",
    "tzdata",
)

WECHAT_ROOTS = (
    r"C:\Program Files\Tencent",
    r"C:\Program Files (x86)\Tencent",
    r"%LOCALAPPDATA%\Tencent",
    r"%APPDATA%\Tencent",
)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _setup_console() -> None:
    """保证 Windows 控制台里的中文能正常显示（GBK 代码页也不炸）。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()


def _read_text_any(path: Path) -> str:
    """仓库里有历史遗留的 GBK 文件，按 utf-8 → gb18030 → latin-1 依次尝试。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


class Report:
    """收集每一步的结果，最后统一打印。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.hints: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        self.rows.append((label, "ok", detail))

    def skip(self, label: str, detail: str = "") -> None:
        self.rows.append((label, "skip", detail))

    def warn(self, label: str, detail: str) -> None:
        self.rows.append((label, "warn", detail))
        self.warnings.append(f"{label}: {detail}")

    def fail(self, label: str, detail: str) -> None:
        self.rows.append((label, "fail", detail))
        self.failures.append(f"{label}: {detail}")

    def hint(self, text: str) -> None:
        if text not in self.hints:
            self.hints.append(text)


# --------------------------------------------------------------------------- #
# 主体
# --------------------------------------------------------------------------- #
class Bootstrap:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.report = Report()
        self.state = self._load_state()
        self.venv_dir: Path | None = None
        self.python: Path | None = None  # 虚拟环境里的解释器
        self._client_cache: list[tuple[str, str]] | None = None

    # -- 状态缓存 ---------------------------------------------------------- #
    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        if self.args.check:
            return
        self.state["schema"] = 1
        self.state["updated_at"] = _now()
        self.state["venv"] = str(self.venv_dir.relative_to(ROOT)) if self.venv_dir else ""
        self.state["python"] = _py_tag()
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _state_installed(self, name: str) -> dict:
        return dict((self.state.get("installed") or {}).get(name) or {})

    def _mark_installed(self, name: str, fingerprint: str, status: str) -> None:
        self.state.setdefault("installed", {})[name] = {
            "fingerprint": fingerprint,
            "status": status,
            "at": _now(),
        }

    # -- 外部命令 ---------------------------------------------------------- #
    def run(
        self,
        cmd: list[str],
        *,
        quiet: bool = False,
        timeout: int | None = None,
        mutating: bool = True,
    ) -> tuple[int, str]:
        """跑一个子进程。

        ``mutating=False`` 表示这是只读探针（import 检查之类），``--check``
        体检模式下也照跑；会改环境的命令在体检模式下直接跳过。
        """
        if self.args.check and mutating:
            return 0, ""
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        if quiet:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return proc.returncode, proc.stdout or ""
        print(f"    $ {' '.join(_quote(c) for c in cmd)}", flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=timeout)
        return proc.returncode, ""

    def pip(self, *args: str, quiet: bool = False, timeout: int | None = None) -> tuple[int, str]:
        assert self.python is not None
        # 注意顺序：-i 必须跟在子命令后面，`pip --no-input -i URL install` 会报
        # "no such option: -i"。
        cmd = [str(self.python), "-m", "pip", "--no-input", *args]
        if self.args.mirror in MIRRORS:
            cmd += ["-i", MIRRORS[self.args.mirror]]
        return self.run(cmd, quiet=quiet, timeout=timeout)

    def pip_install_file(self, req: Path) -> tuple[bool, str]:
        """装一个 requirements 文件；直连 PyPI 失败时自动换国内镜像重试。"""
        code, out = self.pip("install", "-r", str(req), quiet=True)
        if code == 0:
            return True, out
        if self.args.mirror in MIRRORS:
            return False, out
        print("  [pip] 直连 PyPI 失败，自动改用清华镜像重试 …", flush=True)
        original = self.args.mirror
        self.args.mirror = "tsinghua"
        code2, out2 = self.pip("install", "-r", str(req), quiet=True)
        if code2 == 0:
            self.state["mirror"] = "tsinghua"
            self.report.hint("本机直连 PyPI 不稳定，已自动改用清华镜像（下次会用同一个）")
            return True, out2
        self.args.mirror = original
        return False, out

    # -- 1. Python --------------------------------------------------------- #
    def step_python(self) -> bool:
        version = sys.version_info
        bits = platform.architecture()[0]
        detail = f"Python {version.major}.{version.minor}.{version.micro} ({bits})"
        if version[:2] < MIN_PYTHON:
            self.report.fail(
                "Python",
                f"{detail} 版本过低，需要 3.11+；装好再重跑本脚本",
            )
            return False
        if version[:2] < RECOMMENDED_PYTHON:
            self.report.warn("Python", f"{detail}，建议 3.11 / 3.12（3.13+ 部分依赖还没轮子）")
        elif IS_WINDOWS and bits != "64bit":
            self.report.fail(
                "Python",
                f"{detail} 是 32 位，微信 4.x 适配器要 64 位；换 64 位 Python 重装",
            )
            return False
        else:
            self.report.ok("Python", detail)
        if IS_WINDOWS and version[:2] >= (3, 13):
            self.report.hint("Python 3.13+ 部分依赖可能没有预编译轮子，建议用 3.11 / 3.12")
        return True

    # -- 2. 虚拟环境 ------------------------------------------------------- #
    def step_venv(self) -> bool:
        candidates = [ROOT / ".venv312", ROOT / ".venv"]
        existing = next((c for c in candidates if _venv_python(c).exists()), None)
        if existing:
            self.venv_dir = existing
            self.python = _venv_python(existing)
            self.report.ok("虚拟环境", f"{existing.name}（已存在，跳过创建）")
            return True

        # Windows + 3.12 建 .venv312（跟现有脚本的约定一致）；其他情况建 .venv
        if IS_WINDOWS and sys.version_info[:2] == (3, 12):
            target = candidates[0]
        else:
            target = candidates[1]
        if self.args.check:
            self.report.warn("虚拟环境", "不存在，首次启动会自动创建")
            return True
        print(f"  [venv] 正在创建 {target.name} …", flush=True)
        code, out = self.run([sys.executable, "-m", "venv", str(target)])
        if code != 0:
            self.report.fail("虚拟环境", f"创建失败（{target.name}）")
            if out.strip():
                print(out[-2000:])
            return False
        self.venv_dir = target
        self.python = _venv_python(target)
        if not self.python.exists():
            self.report.fail("虚拟环境", f"{target.name} 建好了但没有解释器，删掉重跑")
            return False
        self.report.ok("虚拟环境", f"{target.name}（刚创建）")
        self.state.pop("installed", None)  # 新环境要重装
        return True

    # -- 3. 基础依赖 ------------------------------------------------------- #
    def _needs_install(self, name: str, fingerprint: str, probe=None) -> bool:
        if self.args.force:
            return True
        record = self._state_installed(name)
        if record.get("fingerprint") == fingerprint and record.get("status") == "ok":
            return False
        if not record and probe is not None and self.python is not None and probe():
            # 没有缓存但依赖其实都在（比如环境是从别的机器整份拷来的），
            # 就别白等一遍 pip install。
            self._mark_installed(name, fingerprint, "ok")
            return False
        return True

    def _fingerprint(self, req_file: Path) -> str:
        return "|".join(
            (
                _sha256_file(req_file) or req_file.name,
                _py_tag(),
                platform.platform(),
            )
        )

    def step_requirements(self) -> bool:
        if self.args.check:
            record = self._state_installed("requirements.txt")
            if record.get("status") == "ok":
                self.report.ok("基础依赖", "已安装 requirements.txt")
            else:
                missing = self._missing_imports() if self.python else list(BASE_IMPORTS)
                if missing:
                    self.report.warn(
                        "基础依赖",
                        f"缺 {len(missing)} 个包，首次启动会自动 pip install -r requirements.txt",
                    )
                else:
                    self.report.ok("基础依赖", "已安装（import 自检通过）")
            return True

        assert self.python is not None
        # pip 自检：有些发行版自带的 venv 没装 pip
        code, _ = self.run([str(self.python), "-m", "pip", "--version"], quiet=True)
        if code != 0:
            print("  [pip] 补装 pip …", flush=True)
            self.run([str(self.python), "-m", "ensurepip", "--upgrade"], quiet=True)

        if not self.state.get("pip_upgraded"):
            self.pip("install", "--upgrade", "pip", "setuptools", "wheel", quiet=True)
            self.state["pip_upgraded"] = True

        req = ROOT / "requirements.txt"
        fingerprint = self._fingerprint(req)
        if self._needs_install(
            "requirements.txt", fingerprint, probe=lambda: not self._missing_imports()
        ):
            print("  [pip] 安装基础依赖（首次约 1-2 分钟）…", flush=True)
            ok, out = self.pip_install_file(req)
            if not ok:
                self.report.fail("基础依赖", "pip install -r requirements.txt 失败")
                print(out[-4000:])
                self.report.hint(
                    "网络受限可加 --mirror tsinghua 重试，或手动 pip install -r requirements.txt"
                )
                return False
            self._mark_installed("requirements.txt", fingerprint, "ok")
            self.report.ok("基础依赖", "已安装 requirements.txt")
        else:
            self.report.ok("基础依赖", "已安装（缓存命中）")

        if self.args.skip_verify:
            return True
        missing = self._missing_imports()
        if not missing:
            self.report.ok("依赖自检", "核心模块 import 正常")
            return True
        print(f"  [pip] 依赖自检发现缺失：{', '.join(missing)}，重装中 …", flush=True)
        ok, out = self.pip_install_file(req)
        if ok and not self._missing_imports():
            self._mark_installed("requirements.txt", fingerprint, "ok")
            self.report.ok("依赖自检", "自动修复完成")
            return True
        self.report.fail("依赖自检", f"仍缺少：{', '.join(missing) or '未知'}")
        if out.strip():
            print(out[-4000:])
        return False

    def _missing_imports(self) -> list[str]:
        if self.python is None:
            return list(BASE_IMPORTS)
        probe = "import importlib,sys\nmissing=[]\n" + "\n".join(
            f"try: importlib.import_module('{m}')\nexcept Exception: missing.append('{m}')"
            for m in BASE_IMPORTS
        ) + "\nprint(','.join(missing))"
        code, out = self.run([str(self.python), "-c", probe], quiet=True, timeout=180, mutating=False)
        if code != 0:
            return list(BASE_IMPORTS)
        line = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
        return [m for m in line.split(",") if m]

    def _import_ok(self, modules: tuple[str, ...]) -> bool:
        if not modules or self.python is None:
            return False
        probe = "import importlib\nok=True\n" + "\n".join(
            f"try: importlib.import_module('{m}')\nexcept Exception: ok=False" for m in modules
        ) + "\nprint('1' if ok else '0')"
        code, out = self.run([str(self.python), "-c", probe], quiet=True, timeout=120, mutating=False)
        return code == 0 and (out or "").strip().endswith("1")

    def _extra_present(self, name: str) -> bool:
        if self._state_installed(name).get("status") == "ok":
            return True
        return name in EXTRA_PROBES and self._import_ok(EXTRA_PROBES[name])

    # -- 4. 功能模块依赖 --------------------------------------------------- #
    def step_extras(self) -> bool:
        wanted = self._resolve_extras()
        if self.args.check:
            present = [name for name in EXTRAS if self._extra_present(name)]
            missing = [name for name in wanted if name not in present]
            if missing:
                self.report.warn("功能模块", "未装：" + "、".join(missing) + "（可 --extras 安装）")
            else:
                self.report.ok("功能模块", "、".join(sorted(present)) or "无（--extras all 可全装）")
            return True

        if not wanted:
            self.report.ok("功能模块", "按需跳过（--extras all 可装全）")
            return True

        for name in wanted:
            req_name, label = EXTRAS[name]
            req = ROOT / req_name
            if not req.exists():
                self.report.warn(name, f"缺少 {req_name}")
                continue
            fingerprint = self._fingerprint(req)
            probes = EXTRA_PROBES.get(name, ())
            if not self._needs_install(name, fingerprint, probe=lambda p=probes: self._import_ok(p)):
                self.report.ok(name, f"{label}（已装）")
                continue
            print(f"  [extras] 安装 {name}：{label} …", flush=True)
            ok, out = self.pip_install_file(req)
            if ok:
                self._mark_installed(name, fingerprint, "ok")
                self.report.ok(name, label)
            else:
                self._mark_installed(name, fingerprint, "failed")
                tail = "\n".join((out or "").strip().splitlines()[-3:])
                self.report.warn(name, f"装失败（不影响启动）：{tail[:200]}")
                self.report.hint(f"单独重试：python -m pip install -r {req_name}")
        return True

    def _resolve_extras(self) -> list[str]:
        choice = (self.args.extras or "auto").strip().lower()
        if choice in ("none", "no", "0"):
            return []
        if choice in ("all", "yes"):
            if IS_WINDOWS:
                return list(EXTRAS)
            return [n for n in EXTRAS if n in ("bilibili", "memory", "ocr")]
        if choice == "auto":
            names: list[str] = []
            if IS_WINDOWS:
                names.append("replica")  # 真实微信 4.x 的主力通道
                auto_login = (_parse_env_values(ENV_FILE).get("AUTO_LOGIN") or "").lower()
                if auto_login == "wcf":
                    names.append("wcf")
            if self.args.with_ocr:
                names.append("ocr")
            return names
        names = [n.strip() for n in choice.replace(" ", "").split(",") if n.strip()]
        unknown = [n for n in names if n not in EXTRAS]
        if unknown:
            self.report.warn("功能模块", f"未知模块 {unknown}，可选：{list(EXTRAS)}")
        return [n for n in names if n in EXTRAS]

    # -- 5. .env ----------------------------------------------------------- #
    def step_env(self) -> bool:
        required_defaults = {
            "HOST": "127.0.0.1",
            "PORT": "8000",
            "AUTO_LOGIN": "",
            "LLM_BASE_URL": "https://api.deepseek.com/v1",
            "LLM_API_KEY": "",
            "LLM_MODEL": "deepseek-chat",
            "SEED_SAMPLE_STICKERS": "true",
        }
        if not ENV_FILE.exists():
            if self.args.check:
                self.report.warn(".env", "不存在，首次启动会从 .env.example 生成")
                return True
            text = _read_text_any(ENV_EXAMPLE) if ENV_EXAMPLE.exists() else ""
            if not text.strip():
                text = "\n".join(f"{k}={v}" for k, v in required_defaults.items()) + "\n"
            ENV_FILE.write_text(text, encoding="utf-8")
            self.report.ok(".env", "已从 .env.example 生成（UTF-8）")
        else:
            self.report.ok(".env", "已存在（保留你的配置）")

        # 只补缺失的键，绝不覆盖已有值
        existing = _parse_env_keys(ENV_FILE)
        missing = [k for k in required_defaults if k not in existing]
        if missing and not self.args.check:
            with ENV_FILE.open("a", encoding="utf-8") as fh:
                fh.write("\n# 由 bootstrap 补齐的默认项\n")
                for key in missing:
                    fh.write(f"{key}={required_defaults[key]}\n")
            self.report.ok(".env", "补齐缺失键：" + "、".join(missing))

        values = _parse_env_values(ENV_FILE)
        if values.get("LLM_API_KEY"):
            self.report.ok("模型 Key", "已配置")
        else:
            self.report.warn("模型 Key", "LLM_API_KEY 为空，群里不会接话")
            self.report.hint("填模型 Key：编辑 .env 的 LLM_API_KEY，或控制台「设置 → 大模型」里填")
        if self._data_dir_override(values.get("DATA_DIR")):
            self.report.warn("data 目录", "DATA_DIR 指向别处，骨架会建在那个目录")
        return True

    def _data_dir_override(self, value: str | None) -> bool:
        return bool(value and Path(value).as_posix().rstrip("/") not in ("./data", "data"))

    def step_dirs(self) -> bool:
        if self.args.check:
            self.report.ok("data/ 目录", "检查模式跳过创建")
            return True
        created = 0
        for rel in DATA_DIRS:
            path = ROOT / rel
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created += 1
        self.report.ok("data/ 目录", f"{len(DATA_DIRS)} 个目录就绪" + (f"（新建 {created}）" if created else ""))
        return True

    # -- 6. 数据库 --------------------------------------------------------- #
    def step_db(self) -> bool:
        if self.args.check:
            db = ROOT / "data" / "agent.db"
            if db.exists():
                self.report.ok("数据库", "data/agent.db 已存在")
            else:
                self.report.warn("数据库", "未初始化，首次启动会自动建表")
            return True
        if self.python is None:
            self.report.warn("数据库", "没有可用解释器，跳过初始化")
            return True
        code, out = self.run([str(self.python), "-c", _DB_INIT_SNIPPET], quiet=True, timeout=180)
        payload: dict = {}
        if code == 0:
            for line in reversed((out or "").strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        payload = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
        if payload.get("ok"):
            self.report.ok(
                "数据库",
                f"data/agent.db 就绪（{payload.get('tables', 0)} 张表）",
            )
            return True
        self.report.warn("数据库", "init_db 未成功，服务启动时会再试一次")
        if out.strip():
            print(out.strip()[-1500:])
        return True  # 服务启动时 lifespan 还会建表，不阻塞

    # -- 7. 运行时 / 客户端 ------------------------------------------------ #
    def step_runtime(self) -> bool:
        self._check_dsh()
        self._check_wechat()
        return True

    def _check_dsh(self) -> None:
        runtimes = ROOT / "data" / "dsh" / "runtimes"
        installed = sorted(p.name for p in runtimes.iterdir() if p.is_dir()) if runtimes.exists() else []
        present = bool(installed)
        label = "已安装 " + "、".join(installed[-3:]) if present else "未安装"

        if self.args.check:
            if present:
                self.report.ok("DSH 运行时", label)
            else:
                self.report.warn("DSH 运行时", "未安装，Agent 能力不可用")
                self.report.hint("装 DSH：python scripts\\setup_dsh.py --channel stable（约 700MB）")
            return

        if present and not self.args.with_dsh:
            self.report.ok("DSH 运行时", label)
            return

        if not present:
            want = self.args.with_dsh or (
                not self.args.no_input and _ask("现在安装 DSH 运行时（约 700MB，需联网）？")
            )
            if not want:
                self.report.warn("DSH 运行时", "未安装，Agent 能力暂不可用")
                self.report.hint("随时补装：python scripts\\setup_dsh.py --channel stable")
                return

        print("  [dsh] 安装 / 更新 DeepSeek Harness 运行时（约 700MB，慢一点）…", flush=True)
        if self.python is None:
            self.report.warn("DSH 运行时", "没有可用解释器，跳过")
            return
        code, out = self.run(
            [str(self.python), str(ROOT / "scripts" / "setup_dsh.py"), "--channel", "stable"],
            quiet=True,
            timeout=None,
        )
        if code == 0:
            self.report.ok("DSH 运行时", "安装完成" if not present else "已更新")
        else:
            self.report.warn("DSH 运行时", "安装失败（不影响启动，Agent 能力待补）")
            if out.strip():
                print(out.strip()[-1500:])
            self.report.hint("重试：python scripts\\setup_dsh.py --channel stable")

    def _detect_clients(self) -> list[tuple[str, str]]:
        """返回 [(exe 路径, '4.x' / '3.9')]。

        客户端可以装在任意盘、任意目录（4.x 甚至支持绿化版），所以只查默认
        路径会误判成「没装」。这里按可执行文件名认，先扫常见目录，再扫各盘
        根目录下的一级文件夹。
        """
        if not IS_WINDOWS:
            return []
        found: list[tuple[str, str]] = []
        seen_exe: set[str] = set()
        for base in self._client_dirs():
            for pattern in ("*.exe", "*/*.exe"):
                try:
                    candidates = list(base.glob(pattern))
                except OSError:
                    continue
                for exe in candidates:
                    key = str(exe).lower()
                    if key in seen_exe:
                        continue
                    seen_exe.add(key)
                    name = exe.name.lower()
                    if name == "weixin.exe":
                        found.append((str(exe), "4.x"))
                    elif name == "wechat.exe":
                        found.append((str(exe), "3.9"))
        return found

    def _client_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[str] = set()

        def add(candidate: Path) -> None:
            key = str(candidate).lower()
            if key in seen or not candidate.is_dir():
                return
            seen.add(key)
            dirs.append(candidate)

        for root in WECHAT_ROOTS:
            add(Path(os.path.expandvars(root)))
        for drive in _drive_roots():
            for name in (
                "Weixin",
                "WeChat",
                "Tencent",
                "Program Files\\Tencent",
                "Program Files (x86)\\Tencent",
                "Program Files\\Weixin",
            ):
                add(drive / name)
        return dirs

    @property
    def clients(self) -> list[tuple[str, str]]:
        if self._client_cache is None:
            self._client_cache = self._detect_clients()
        return self._client_cache

    def _wechat_major(self) -> str:
        clients = self.clients
        if any(family.startswith("4") for _, family in clients):
            return "4"
        return "3" if clients else ""

    def _check_wechat(self) -> None:
        if not IS_WINDOWS:
            self.report.skip("客户端", "非 Windows，用演示模式即可")
            return
        clients = self.clients
        data_dir = Path(os.path.expandvars(r"%APPDATA%\Tencent\xwechat"))
        if clients:
            families = "、".join(sorted({family for _, family in clients}))
            where = "；".join(path for path, _ in clients[:2])
            self.report.ok("客户端", f"检测到 {families}：{where}")
        elif data_dir.is_dir():
            self.report.ok("客户端", "检测到 4.x 数据目录（程序在自定义路径，正常）")
        else:
            self.report.warn("客户端", "没找到微信安装痕迹")
            self.report.hint(
                "装微信 4.1.12+ 后在控制台选「微信 4.x」扫码登录；不装也能先用演示模式"
            )

    # -- 汇总 -------------------------------------------------------------- #
    def summary(self) -> None:
        icon = {"ok": "OK  ", "skip": "--  ", "warn": "WARN", "fail": "FAIL"}
        line = "-" * 62
        print()
        print("=" * 62)
        print("  群伴 · 环境自举结果" if not self.args.check else "  群伴 · 环境体检结果")
        print("=" * 62)
        for label, status, detail in self.report.rows:
            print(f"  [{icon[status]}] {label:<12} {detail}")
        print(line)
        if self.report.hints:
            print("  下一步：")
            for hint in self.report.hints:
                print(f"    · {hint}")
            print(line)
        if self.report.failures:
            print(f"  失败 {len(self.report.failures)} 项，按上面的提示修好再启动。")
        elif self.report.warnings:
            print(f"  可以启动，另有 {len(self.report.warnings)} 项提示（不影响运行）。")
        else:
            print("  一切就绪，直接启动即可。")
        print("=" * 62)


_DB_INIT_SNIPPET = r"""
import asyncio, json, sqlite3, sys
sys.path.insert(0, ".")
async def _main():
    from app.config import settings
    from app.db import init_db, dispose_db
    await init_db()
    await dispose_db()
    path = str(settings.db_path)
    con = sqlite3.connect(path)
    tables = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    con.close()
    print(json.dumps({"ok": True, "db": path, "tables": tables}))
try:
    asyncio.run(_main())
except Exception as exc:
    print(json.dumps({"ok": False, "error": repr(exc)}))
    raise SystemExit(1)
"""


def _py_tag() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}-{platform.architecture()[0]}"


def _quote(token: str) -> str:
    return f'"{token}"' if " " in token else token


def _parse_env_lines(path: Path) -> list[str]:
    try:
        return _read_text_any(path).splitlines()
    except OSError:
        return []


def _parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in _parse_env_lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def _parse_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _parse_env_lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _drive_roots() -> list[Path]:
    """当前系统上真实存在的盘符根目录（用于找装在 D:\\ 这种地方的客户端）。"""
    if not IS_WINDOWS:
        return []
    roots: list[Path] = []
    try:
        letters = list(os.listdrives())  # Python 3.12+
    except AttributeError:
        letters = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"]
    for letter in letters:
        path = Path(letter if letter.endswith("\\") else letter + "\\")
        try:
            if path.is_dir():
                roots.append(path)
        except OSError:
            continue
    return roots


def _ask(question: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        answer = input(f"  {question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes", "1", "是")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="群伴首次启动自举：把 git clone 缺的环境一次补齐（可重复运行）",
    )
    parser.add_argument("--check", action="store_true", help="只体检，不改动任何文件")
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制重装依赖")
    parser.add_argument("--skip-verify", action="store_true", help="跳过依赖 import 自检")
    parser.add_argument(
        "--extras",
        default="auto",
        help="功能模块：auto / none / all / 逗号列表（%s）" % ",".join(EXTRAS),
    )
    parser.add_argument("--with-ocr", action="store_true", help="auto 模式下也装离线 OCR")
    parser.add_argument("--with-dsh", action="store_true", help="顺带安装 DSH 运行时（约 700MB）")
    parser.add_argument("--mirror", default="none", choices=("none", *MIRRORS), help="PyPI 镜像")
    parser.add_argument("--no-input", action="store_true", help="不交互（CI / 后台启动用）")
    parser.add_argument("--json", action="store_true", help="最后输出一行 JSON 摘要")
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    if args.json:
        args.no_input = True

    boot = Bootstrap(args)
    # 上次自动切换过的镜像记住，省得每次都先撞一遍超时
    remembered = boot.state.get("mirror")
    if args.mirror == "none" and remembered in MIRRORS:
        args.mirror = remembered
    print()
    print("  群伴 · 环境自举" + ("（检查模式）" if args.check else ""))
    print(f"  项目目录 {ROOT}")
    print()

    if boot.step_python():
        if boot.step_venv():
            boot.step_requirements()
            boot.step_extras()
            boot.step_env()
            boot.step_dirs()
            boot.step_db()
            boot.step_runtime()

    boot.summary()
    boot._save_state()

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not boot.report.failures,
                    "failures": boot.report.failures,
                    "warnings": boot.report.warnings,
                    "venv": str(boot.venv_dir.name) if boot.venv_dir else "",
                    "rows": [{"label": l, "status": s, "detail": d} for l, s, d in boot.report.rows],
                },
                ensure_ascii=False,
            )
        )
    return 1 if boot.report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
