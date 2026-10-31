"""Turn a technical failure into one line a WeChat user can act on.

Every task failure ends up as a chat message, so the message has to answer
"why did nothing happen" — pasting a registration stack or a spawn errno made
users report the bot as "died silently". Raw detail still goes to logs and to
``agent_task_runs.error``; this module only owns the human sentence.
"""

from __future__ import annotations

# (needle, human sentence) — checked in order, first hit wins.
_RULES: tuple[tuple[str, str], ...] = (
    (
        'service "shell" has been registered',
        "本机命令通道没起来（DSH 的 shell 插件冲突），这一步没有真正执行",
    ),
    ("spawn bash enoent", "本机没有可用的 bash，命令没能执行"),
    ("spawn pwsh enoent", "本机没找到 PowerShell 7（pwsh）"),
    ("发送消息失败", "结果没能发出去（微信窗口/会话状态不对），我卡在最后一步"),
    ("不是「", "发送时微信串到了别的会话，为了不发错群我先停下了"),
    ("sandbox", "沙箱执行器不可用，命令被拦下了"),
    ("timeout", "某一步执行超时，我先停了下来"),
    ("超时", "某一步执行超时，我先停了下来"),
    ("not found", "要用的文件或命令没找到"),
    ("permission", "没有权限执行这一步"),
    ("微信窗口", "操作微信窗口时出了问题（窗口被最小化或被挡住）"),
    ("模型未调用任何工具", "模型这一轮没有真正动手，结果不可信"),
)


def explain_failure(exc: BaseException | str) -> str:
    """Return a short, human-readable reason for a task failure."""
    text = str(exc or "")
    low = text.lower()
    for needle, human in _RULES:
        if needle in low or needle in text:
            return human
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return lines[0][:120]
    return type(exc).__name__ if isinstance(exc, BaseException) else "未知原因"


def failure_notice(task_id: int | None, exc: BaseException | str, *, retry_hint: bool = True) -> str:
    """Build the message sent to the user when a task fails."""
    prefix = f"任务 #{task_id} " if task_id is not None else ""
    body = f"{prefix}没做成。\n原因：{explain_failure(exc)}"
    if retry_hint:
        body += "\n详细报错已经记到后台「任务」列表里；回我一句「重试」我就再跑一遍。"
    return body
