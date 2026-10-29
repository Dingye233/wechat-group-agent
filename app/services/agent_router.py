"""Deterministic, low-token routing rules.

The router chooses a lane; it never writes user-facing content.  It is the
cheap first stage before DSH: rules handle the clear cases, and an ambiguous
case may later be escalated to a tiny classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TASK_MARKERS = (
    "帮我",
    "帮忙",
    "查一下",
    "查查",
    "搜索",
    "搜一下",
    "找一下",
    "读取",
    "读一下",
    "写一个",
    "写个",
    "做一个",
    "做个",
    "生成",
    "整理",
    "总结一下",
    "分析",
    "翻译",
    "计算",
    "安装",
    "部署",
    "修复",
    "调试",
    "运行",
    "执行",
    "发送",
    "发一下",
    "上传",
    "下载",
    "导出",
    "导入",
    "创建",
    "删除",
    "修改",
    "更新",
    "插件",
    "文件",
    "表格",
    "文档",
    "代码",
    "脚本",
    "任务",
    "待办",
    "提醒",
    "催",
    "定时",
    "爬",
    "接口",
    "知识库",
    "记忆",
)
CHAT_MARKERS = (
    "哈哈",
    "在吗",
    "在不在",
    "早上好",
    "早安",
    "晚安",
    "谢谢",
    "好的",
    "收到",
    "辛苦了",
    "牛",
    "厉害",
    "笑死",
    "真的假的",
    "怎么说",
    "你觉得",
)
TASK_RE = re.compile(
    r"(帮我|帮忙|查|搜|找|读|写|做|生成|整理|总结|分析|翻译|计算|安装|部署|修复|调试|运行|执行|发送|上传|下载|导出|导入|创建|删除|修改|更新|插件|文件|表格|文档|代码|脚本|任务|待办|提醒|催|定时|爬|接口|知识库|记忆)"
)


@dataclass(frozen=True)
class Route:
    lane: str
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def classify(text: str, *, scope: str = "group", has_tools: bool = True) -> Route:
    value = re.sub(r"\s+", "", str(text or ""))
    if not value:
        return Route("ignore", 1.0, "空消息")
    if not has_tools:
        return Route("chat", 0.9, "当前没有工具，只能聊天")
    if any(marker in value for marker in TASK_MARKERS) or TASK_RE.search(value):
        return Route("task", 0.82, "命中任务/工具动作词")
    if any(marker in value for marker in CHAT_MARKERS):
        return Route("chat", 0.9, "命中闲聊词")
    if len(value) <= 30:
        return Route("chat", 0.68, "短消息默认聊天")
    return Route("chat", 0.55, "未命中任务词，默认聊天")


def classify_group(text: str, *, has_tools: bool = True) -> Route:
    return classify(text, scope="group", has_tools=has_tools)


def classify_private(text: str, *, has_tools: bool = True) -> Route:
    return classify(text, scope="private", has_tools=has_tools)
