"""Cache-friendly prompt assembly for every LLM call in the app.

DeepSeek (and most OpenAI-compatible providers) bill context caching on the
*longest matching prefix* of the request: an identical beginning is served from
cache, and the first changed byte invalidates everything after it. A prompt
that starts with the current time, the current group, the latest memory or the
latest message therefore has a cache hit rate close to zero even if 90% of its
text is boilerplate.

Every prompt in 群伴 is assembled in three layers, in this order:

    static   -> code-owned text, identical for every request of that kind
    context  -> per group / per contact / per capability set, changes rarely
    dynamic  -> time, memory, impressions, current plan, current messages

``PromptBuilder`` enforces that order, and ``Prompt.cache_prefix_chars`` tells
the LLM client how many leading characters are stable across requests so the
cache hit rate can be measured per call site (``/api/llm/usage``).

Rules for adding new prompts:

1. Never put ``datetime.now()``, random values, memory, message history or a
   per-turn plan above ``builder.dynamic()``.
2. Things that change when the user edits settings belong in ``context``, not
   ``static`` or ``dynamic``.
3. Optional blocks may be omitted freely when they sit at the *end* of their
   layer: that only shortens the tail, it does not move the stable prefix.
4. Keep the text stable across refactors. Re-wording a static block once costs
   one cold call per model; re-ordering it costs a cold call on every deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Bump when the layout itself changes; only useful for logs.
PROMPT_LAYOUT_VERSION = 2


def section(title: str, body: Any) -> str:
    """Render a markdown-ish section, returning "" when there is no body."""
    text = str(body or "").strip()
    if not text:
        return ""
    return f"# {title}\n{text}"


@dataclass(frozen=True)
class Prompt:
    """A finished prompt plus the length of its cacheable prefix."""

    name: str
    system: str
    cache_prefix_chars: int

    def __str__(self) -> str:  # convenience for callers that pass a string
        return self.system

    @property
    def cache_prefix_ratio(self) -> float:
        if not self.system:
            return 0.0
        return self.cache_prefix_chars / len(self.system)


class PromptBuilder:
    """Layered prompt builder. Order is enforced by construction."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._static: list[str] = []
        self._context: list[str] = []
        self._dynamic: list[str] = []

    def static(self, *parts: str) -> "PromptBuilder":
        self._static.extend(part for part in parts if str(part or "").strip())
        return self

    def context(self, *parts: str) -> "PromptBuilder":
        self._context.extend(part for part in parts if str(part or "").strip())
        return self

    def dynamic(self, *parts: str) -> "PromptBuilder":
        self._dynamic.extend(part for part in parts if str(part or "").strip())
        return self

    def build(self) -> Prompt:
        stable = _join(self._static + self._context)
        tail = _join(self._dynamic)
        system = _join([stable, tail])
        return Prompt(
            name=self.name,
            system=system,
            cache_prefix_chars=len(stable),
        )


def _join(parts: list[str]) -> str:
    cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
    return "\n\n".join(cleaned)


# ---------------------------------------------------------------------------
# Group chat: reply and proactive initiation
# ---------------------------------------------------------------------------

GROUP_REPLY_RULES = """# 回复规则
1. 你是在微信群里自然地接话，回复最新一条消息，不要像客服或机器人。
2. 通常 1-2 句，最多 80 个汉字；不要使用 Markdown，不要分点列清单。
3. 不要复述或总结整段聊天，除非有人明确要求总结。
4. 不确定的事实不要编造；涉及隐私、金钱、承诺时保持谨慎。
5. 可以结合上下文和成员昵称，但不要叫错人。
6. 普通模式最多选择一个表情包；连续消息模式可穿插表情包，但不要每条都发。
7. 只输出一个 JSON 对象，不要输出 Markdown 代码块或其他文字。
8. 先直接回应最后一条消息的具体问题；不要用玩笑、人设或旧话题回避。用户在反馈卡顿、答非所问或工具问题时，先说明你实际看到了什么、准备怎么处理。
9. 同一个人连续发多条时，把它们当成一个完整意图回答，不要只抓最后两个字。涉及查询/泄露他人个人信息、手机号、住址等隐私时，明确说明群聊里不能提供，不要编造。"""

GROUP_REPLY_PROTOCOL = """# 输出协议
只输出一个 JSON 对象，不要输出 Markdown 代码块或其他文字；具体字段以「本轮输出格式」为准。
不回复时只输出 {"action": "none"}。"""

GROUP_REPLY_STYLE = """# 输出风格
- 不要输出工具名、工具调用过程、OBSERVATION、🔧、JSON 字段名或分析过程。
- 需要工具时，用“我去看看”“我查一下”这类自然话过渡；只给最终结果。
- 缺少的能力不是“无”时，直接如实说明当前做不到，不要编造结果或假装完成。
- 需要生成表格时调用 create_excel，把内容整理成 sheets[].rows，第一行是表头；不要调用屏幕/鼠标/键盘类工具。
- 像真人聊天，带一点符合当前情绪的语气，但不要夸张表演。"""

STICKER_NOTE = "sticker 填表情包 id、名称或标签，也可为 null。只有当表情包确实贴合语境时才使用。"

GROUP_TOOL_RULES = """工具结果会以 OBSERVATION 返回，然后你再决定回复或继续调用。
参数键必须与上面的工具列表完全一致；如果 OBSERVATION 以 ERROR 开头，先修正参数重试，不要编造结果。
群聊里不要调用屏幕、鼠标、键盘、终端、沙箱或任意路径读写工具；只允许调用上面列出的群聊安全工具。create_excel 只生成表格，不会读取本地文件；生成后会自动把文件发到群里。
不要假装调用过工具；没有拿到 OBSERVATION 就不能声称完成。"""

PROACTIVE_TASK = """# 任务
群聊已经安静了一段时间。请你主动发起一个自然、轻松、和最近话题相关的新话题，像群友一样接话，不要像客服问候，也不要总结聊天记录。不要编造事实；只输出一个 JSON，具体字段以「本轮输出格式」为准。"""


def build_group_reply_prompt(
    *,
    persona: str,
    group_name: str,
    bot_name: str,
    trigger_label: str,
    now_local: str,
    timezone: str,
    members: list[str],
    sticker_catalog: str,
    sticker_enabled: bool,
    tool_lines: list[str],
    resources: list[str],
    tool_example: str,
    api_example: str,
    bili_hint: str,
    extra_prompt: str,
    custom_core: str,
    multi_message_enabled: bool,
    max_messages: int,
    memory_text: str,
    bili_context: str,
    plan: dict[str, Any],
    impression_text: str,
    use_tools: bool,
) -> Prompt:
    builder = PromptBuilder("group.reply")
    core = str(custom_core or "").strip() or GROUP_REPLY_RULES
    builder.static(core, GROUP_REPLY_PROTOCOL, GROUP_REPLY_STYLE)

    if str(persona or "").strip():
        builder.context(section("人设（每次对话自动注入）", persona))
    builder.context(
        section(
            "群聊信息",
            f"- 群名：{group_name or '未知群聊'}\n"
            f"- 你的名字：{bot_name or '小助手'}\n"
            f"- 触发方式：{trigger_label}",
        )
    )

    if multi_message_enabled:
        limit = max(2, min(6, int(max_messages or 3)))
        builder.context(
            section(
                "连续消息",
                f"- 可以像真人一样连续发 1–{limit} 条短消息，每条 1–2 句；"
                "表情包可以穿插在文字之间。",
            )
        )
        format_line = (
            '{"action": "reply", "messages": '
            '[{"text": "第一句"}, {"sticker": 3}, {"text": "第二句"}]}'
        )
    else:
        format_line = (
            '{"action": "reply", "reply": "要发送的文字，可为空", "sticker": 3}'
        )
    builder.context(section("本轮输出格式", f"{format_line}\n{STICKER_NOTE}"))
    builder.context(
        section(
            "可用表情包",
            sticker_catalog if sticker_enabled else "（本群已关闭表情包发送）",
        )
    )
    if str(extra_prompt or "").strip():
        builder.context(
            section("用户自定义提示词（高优先级，必须遵守）", extra_prompt)
        )

    # Tool catalog is optional and therefore kept at the END of the context
    # layer: toggling it only changes the tail, never the stable prefix.
    # B站背景只在新视频入库时变化，属于「易变但低频」的内容：放在 context
    # 层，且位于可选的工具目录之前，避免工具开关/低频更新互相放大失效范围。
    if bili_context:
        builder.context(
            section(
                "最近看过的 B站内容（背景资料，不是新消息）",
                f"{bili_context}\n以上只用于回答“你看了什么/学到了什么”；"
                "不要把它当成当前群聊的新消息。",
            )
        )

    if tool_lines:
        parts = ["\n".join(tool_lines)]
        if resources:
            parts.append("已启用资源：" + "、".join(resources))
        parts.append("当确实需要工具时，输出：\n" + tool_example + api_example)
        parts.append(GROUP_TOOL_RULES + (bili_hint or ""))
        builder.context(
            section("已启用的群聊工具（只有这些能调用）", "\n\n".join(parts))
        )

    builder.dynamic(section("当前时间", f"{now_local}（{timezone}）"))
    builder.dynamic(
        section(
            "长期记忆（每次对话自动注入）",
            "以下是过去总结的群聊记忆，可作为背景参考；不要机械复述，也不要编造记忆里没有的内容：\n"
            + (memory_text or "无"),
        )
    )
    builder.dynamic(
        section(
            "本轮意图（内部参考，绝对不要直接复述）",
            f"- 意图：{plan.get('intent', 'chat')}\n"
            f"- 用户情绪：{plan.get('user_emotion', 'neutral')}"
            f"（强度 {float(plan.get('emotion_intensity') or 0):.2f}）\n"
            f"- 回复语气：{plan.get('tone', 'warm')}\n"
            f"- 是否需要工具：{'是' if use_tools else '否'}\n"
            f"- 缺少的能力：{plan.get('missing_capability') or '无'}\n"
            "- 对这个人的印象：\n"
            f"{impression_text}",
        )
    )
    if members:
        builder.dynamic(
            section("群成员昵称参考", "、".join(members[:30]) or "暂未同步")
        )
    if use_tools:
        builder.dynamic(section("本轮工具权限", "本轮允许调用工具。"))
    return builder.build()


def build_group_proactive_prompt(
    *,
    persona: str,
    group_name: str,
    bot_name: str,
    now_local: str,
    timezone: str,
    members: list[str],
    sticker_catalog: str,
    sticker_enabled: bool,
    extra_prompt: str,
    multi_message_enabled: bool,
    max_messages: int,
    memory_text: str,
) -> Prompt:
    builder = PromptBuilder("group.proactive")
    builder.static(GROUP_REPLY_PROTOCOL, PROACTIVE_TASK, GROUP_REPLY_STYLE)

    if str(persona or "").strip():
        builder.context(section("人设（每次对话自动注入）", persona))
    builder.context(
        section(
            "群聊信息",
            f"- 群名：{group_name or '未知群聊'}\n"
            f"- 你的名字：{bot_name or '小助手'}",
        )
    )
    if multi_message_enabled:
        limit = max(2, min(6, int(max_messages or 3)))
        builder.context(
            section("连续消息", f"- 可以拆成 1–{limit} 条短消息，表情包穿插在文字之间。")
        )
        format_line = (
            '{"action":"reply","messages":[{"text":"..."},{"sticker":3}]}'
        )
    else:
        format_line = '{"action":"reply","reply":"...","sticker":3}'
    builder.context(section("本轮输出格式", f"{format_line}\n{STICKER_NOTE}"))
    builder.context(
        section(
            "可用表情包",
            sticker_catalog if sticker_enabled else "（本群已关闭表情包发送）",
        )
    )
    if str(extra_prompt or "").strip():
        builder.context(
            section("用户自定义提示词（高优先级，必须遵守）", extra_prompt)
        )

    builder.dynamic(section("当前时间", f"{now_local}（{timezone}）"))
    builder.dynamic(
        section(
            "长期记忆（每次对话自动注入）",
            "以下是过去总结的群聊记忆，可作为背景参考；不要机械复述：\n"
            + (memory_text or "无"),
        )
    )
    if members:
        builder.dynamic(
            section("群成员昵称参考", "、".join(members[:30]) or "暂未同步")
        )
    return builder.build()


# ---------------------------------------------------------------------------
# Operator (assistant tab / WeChat admin DM)
# ---------------------------------------------------------------------------

OPERATOR_PROTOCOL = """# 输出协议
每次只输出一个 JSON 对象，不要 Markdown：
{"thought":"一句中文进度/判断","action":"工具名","args":{...}}
或
{"thought":"一句中文总结","final":"给用户的最终答复"}
- thought 必填，且要能让管理员看懂你正在做什么。
- 只输出一个完整的 JSON 对象，结尾不要多写右花括号，也不要在 JSON 前后加解释。
- 工具结果会以 OBSERVATION 形式返回；只有拿到 OBSERVATION 才能说完成。
- 文件路径可以是绝对路径，也可以是相对工作区的路径。
- 如果用户只是聊天或问题已经回答完，直接 final。"""

OPERATOR_PROTOCOL_TAIL = (
    "记住：每次只输出一个 JSON 对象；需要工具就输出 action，完成就输出 final。"
)

OPERATOR_DEFAULT_CORE = """你是「群伴 Operator」，一个能直接操作本机、持续自我迭代的中文 Agent。
你不是只会聊天的客服：你可以读写文件、执行 PowerShell、调用外部 HTTP、创建技能、创建任务，并在真实系统里完成多步任务。

# 工作方式（参考 opencode / Codex 的 agent loop）
1. 先理解目标、约束和成功标准；信息不够时先做最小必要探查，不要凭空猜测。
2. 简单任务直接执行；复杂任务在 thought 里维护一个很短的 1/2/3 计划，并在关键节点更新。
3. 每一步只做一个动作：先想清楚要验证什么，再调用工具；拿到 OBSERVATION 后判断是否真的推进了任务。
4. 不要因为一个工具成功就急着收尾。只有目标已经达成、或遇到无法自行解决的阻塞时才输出 final。
5. 工具失败后先阅读错误、换参数或换方案；不要原样重试。相同工具 + 相同参数连续失败 3 次，或连续 5 次返回相同结果，系统会自动打断。
6. 用户中途插入新要求时，把它合并进当前任务继续做；除非新要求明确要求推翻原任务，否则不要从头再来。
7. 只有确实需要用户做决定时才提问；能自己判断的默认值就直接选，并在 final 里说明。
8. 删除、覆盖、停服务、发消息、发布内容等有副作用的操作，先确认目标和影响；不要执行与任务无关的破坏性操作。
9. 最终答复必须基于真实 OBSERVATION：说明做了什么、结果是什么、哪些没成功、下一步建议。不要假装执行过工具。

# 进度可见性
- 你每次调用工具前的 thought 会作为进度同步给管理员，所以 thought 要写成一句简短、具体的中文，例如「先读取配置文件确认端口」。
- 如果任务较长，保持 thought 里的计划可读；不要在 thought 里写长篇推理或敏感信息。
- 管理员可以在任务执行中继续发消息；系统会把新消息作为「中途插入的新要求」交给你。

# 能力边界
- 文件读写和 PowerShell 会影响真实系统。执行删除、覆盖、服务/进程操作前，先在 thought 里确认目标路径和影响。
- 需要管理员权限的命令，把 admin=true 传给 run_powershell；会弹 UAC。
- 自我迭代不是改主程序源码：优先在 skills/、projects/、scripts/ 下创建可复用能力；需要长期任务时用 create_task 记录。
- 外部接口用 web_request，只允许 http/https。
- 不要把密钥、密码、Token 写入普通文件或记忆；只记录“配置在哪里”。"""

OPENCODE_RUNTIME_RULES = """# 运行方式（官方 OpenCode）
你现在由官方 OpenCode Agent 运行时执行，使用原生工具调用，不再输出 JSON 动作协议。
- 群伴自己的工具已经作为 MCP 工具注册到 OpenCode，名字形如 `qunban_list_groups`、`qunban_read_file`、`qunban_run_powershell`，可以直接调用。
- 专用工具使用命名参数，例如 `qunban_read_group(group_id=211, limit=30)`。
- 自动注册的通用工具使用 `args` 对象，例如 `qunban_read_file(args={"path": "a.txt"})`、`qunban_run_powershell(args={"command": "Get-Date"})`。
- 不确定工具名或参数时先调用 `qunban_capabilities`，它会返回可直接使用的 `call_tool`、`call_args`、说明和示例参数；也可以用通用入口 `qunban_tool(name="工具名", args={...})`。
- 群伴原生工具的清单由你管理：需要新增别名、改描述、停用/启用、删除或同步工具时，调用 `qunban_tool_admin`。`handler` 只能引用群伴已有的原生工具；要造真正的新功能，写 `handler_type="script"` 的脚本工具。
- 新建工具后，当前轮立刻用 `qunban_tool(name="新工具", args={...})` 调用；直接入口 `qunban_<新工具>` 会在本轮空闲后自动刷新，下一轮才可用。
- OpenCode 原生 `bash` / `read` / `write` / `edit` / `patch` / `task` 已全部启用；可以在工作区写脚本、运行命令、拆分子任务。
- 工作区：`data/opencode/workspace`；群伴工作区：`data/agent_workspace`（现有 skills/projects/scripts 都在这里）。
- 每次只做能验证的一步；工具失败先读错误再换方案，不要原样重试。
- 最终答复必须基于真实工具结果；没有执行过的事情不要假装完成。
- 有副作用的操作先判断目标和影响；能自己判断的直接执行，群伴 Agent 会处理确认和指挥。
- 微信消息发送只能走群伴的 wechatauto-replica：群里用 `qunban_send_message(group_id=..., text=...)`，私聊用 `qunban_send_message(contact_id=..., text=...)`。最终答复由系统自动发送，不需要再调用发送工具。
- 严禁使用 `qunban_keyboard_*`、`qunban_mouse_*` 或 `qunban_run_powershell` 去操作微信窗口/输入框；这些不是消息发送通道，会导致微信卡死。
- 进度会自动显示给管理员；不要输出长篇思维过程或敏感信息。"""

OPENCODE_TOOL_EXAMPLES = """# 常用工具示例
- 列群：qunban_list_groups()
- 读群：qunban_read_group(group_id=211, limit=30)
- 发群：qunban_send_message(group_id=211, text="...")
- 读文件：qunban_read_file(args={"path": "data/agent_workspace/README.md"})
- 执行命令：qunban_run_powershell(args={"command": "Get-Date"})
- B站搜索：qunban_bili_search(keyword="...", limit=10)
- 保存记忆：qunban_memory_save(content="...", title="...")
- 运行群伴 MCP：qunban_run_mcp(args={"name": "screen_control_mcp", "command": "..."})
- 运行技能：qunban_run_skill(args={"name": "skill-name", "args": ""})
- 调用已登记接口：qunban_call_interface(args={"name": "api-name", "args": {}})
- 管理原生工具：qunban_tool_admin(action="list")
- 新增工具别名：qunban_tool_admin(action="upsert", tool={"name": "read_fast", "handler": "read_file", "description": "快速读取文件", "args_example": {"path": "a.txt"}})
- 通用入口：qunban_tool(name="工具名", args={...})"""


def build_operator_prompt(
    *,
    core: str,
    channel_text: str,
    workspace: str,
    admin_text: str,
    persona_block: str,
    extra_prompt: str,
    tool_catalog: str,
    resource_lines: str,
    limit_text: str,
    memory_lines: str,
) -> Prompt:
    builder = PromptBuilder("assistant.operator")
    builder.static(core or OPERATOR_DEFAULT_CORE, OPERATOR_PROTOCOL)

    builder.context(section("当前通道", channel_text))
    builder.context(
        section(
            "工作区",
            f"{workspace}\n技能目录：{workspace}\\skills\n"
            f"项目目录：{workspace}\\projects\n脚本目录：{workspace}\\scripts\n"
            f"当前进程管理员权限：{admin_text}",
        )
    )
    if str(persona_block or "").strip():
        builder.context(section("人设（每次对话自动注入）", persona_block))
    if str(extra_prompt or "").strip():
        builder.context(
            section("用户自定义提示词（高优先级，必须遵守）", extra_prompt)
        )
    builder.context(
        section("可用工具（JSON，只有这里列出的才能调用）", tool_catalog)
    )
    builder.context(section("已启用的技能 / MCP / 接口", resource_lines))
    builder.context(section("步数限制", limit_text))
    builder.context(OPERATOR_PROTOCOL_TAIL)

    builder.dynamic(
        section(
            "长期记忆（每次对话自动注入，供参考）",
            "以下是从过去对话总结的记忆。把它们当作背景，不要机械复述，也不要编造记忆里没有的内容：\n"
            + (memory_lines or "无"),
        )
    )
    return builder.build()


def build_opencode_operator_prompt(
    *,
    core: str,
    channel_text: str,
    workspace: str,
    admin_text: str,
    tool_names: str,
    resource_block: str,
    persona_block: str,
    extra_prompt: str,
    memory_lines: str,
) -> Prompt:
    builder = PromptBuilder("assistant.opencode")
    builder.static(core or OPERATOR_DEFAULT_CORE, OPENCODE_RUNTIME_RULES)
    builder.static(OPENCODE_TOOL_EXAMPLES)

    builder.context(section("当前通道", channel_text))
    builder.context(
        section(
            "工作区",
            f"{workspace}\n当前进程管理员权限：{admin_text}",
        )
    )
    builder.context(section("已启用的群伴工具名", tool_names))
    builder.context(section("已启用的技能 / MCP / 接口", resource_block))
    if str(persona_block or "").strip():
        builder.context(section("人设（每次对话自动注入）", persona_block))
    if str(extra_prompt or "").strip():
        builder.context(section("用户额外要求（高优先级）", extra_prompt))

    builder.dynamic(
        section(
            "长期记忆（参考，不要机械复述）",
            memory_lines or "无",
        )
    )
    return builder.build()


# ---------------------------------------------------------------------------
# Small talk while a long task owns the session
# ---------------------------------------------------------------------------

SIDE_CHAT_RULES = """你是「群伴」的聊天人格。请直接、自然地回答，像微信聊天一样用简短中文段落；不要调用工具，不要输出 Markdown，不要假装后台任务已经完成，也不要编造进度。如果用户问进度，就结合给出的后台状态回答；如果用户只是闲聊，就正常陪聊。"""


def build_side_chat_prompt(
    *,
    persona_text: str,
    busy: bool,
    status_text: str,
    memory_text: str = "",
) -> Prompt:
    builder = PromptBuilder("assistant.side_chat")
    builder.static(SIDE_CHAT_RULES)
    if str(persona_text or "").strip():
        builder.context(section("人设", persona_text))
    if str(memory_text or "").strip():
        builder.context(section("私聊记忆", memory_text))
    builder.dynamic(
        section(
            "后台状态",
            (
                "现在后台有一个任务正在执行，用户发来的是闲聊、吐槽或状态询问，不是新的任务指令。"
                if busy
                else "现在没有后台任务，你正在和用户闲聊。"
            )
            + f"\n当前后台状态：{status_text}",
        )
    )
    return builder.build()
