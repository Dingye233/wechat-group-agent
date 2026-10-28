from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    # SQLite DateTime columns are timezone-naive; store UTC consistently.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adapter: Mapped[str] = mapped_column(String(32), default="")
    wxid: Mapped[str] = mapped_column(String(128), default="", index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    avatar: Mapped[str] = mapped_column(String(512), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    remark: Mapped[str] = mapped_column(String(160), default="")
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    avatar: Mapped[str] = mapped_column(String(512), default="")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    config: Mapped[GroupConfig] = relationship(
        back_populates="group", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    members: Mapped[list[GroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="selectin"
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "wxid", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    wxid: Mapped[str] = mapped_column(String(128), index=True)
    nickname: Mapped[str] = mapped_column(String(160), default="")
    alias: Mapped[str] = mapped_column(String(160), default="")
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    group: Mapped[Group] = relationship(back_populates="members")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_group_id_id", "group_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True, index=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    sender_wxid: Mapped[str] = mapped_column(String(128), default="")
    sender_name: Mapped[str] = mapped_column(String(160), default="")
    is_from_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(24), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    media_path: Mapped[str] = mapped_column(String(512), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class GroupConfig(Base):
    __tablename__ = "group_configs"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    read_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    read_interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger_mode: Mapped[str] = mapped_column(String(24), default="mention")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=30)
    max_replies_per_hour: Mapped[int] = mapped_column(Integer, default=30)
    persona: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    sticker_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    multi_message_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_messages: Mapped[int] = mapped_column(Integer, default=3)
    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    proactive_interval_minutes: Mapped[int] = mapped_column(Integer, default=90)
    quiet_start: Mapped[str] = mapped_column(String(8), default="")
    quiet_end: Mapped[str] = mapped_column(String(8), default="")
    mention_on_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    last_memory_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    group: Mapped[Group] = relationship(back_populates="config")


class Sticker(Base):
    __tablename__ = "stickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(String(400), default="")
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    mime: Mapped[str] = mapped_column(String(64), default="image/png")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("account_key", "wxid", name="uq_contact_account_wxid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    wxid: Mapped[str] = mapped_column(String(128), default="", index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    remark: Mapped[str] = mapped_column(String(160), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger_mode: Mapped[str] = mapped_column(String(24), default="all")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    persona: Mapped[str] = mapped_column(Text, default="")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DirectMessage(Base):
    __tablename__ = "direct_messages"
    __table_args__ = (
        Index("ix_direct_messages_contact_id_id", "contact_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(
        String(180), nullable=True, unique=True, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), index=True
    )
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    sender_wxid: Mapped[str] = mapped_column(String(128), default="")
    sender_name: Mapped[str] = mapped_column(String(160), default="")
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(24), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    media_path: Mapped[str] = mapped_column(String(512), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class BiliAccount(Base):
    __tablename__ = "bili_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    sessdata: Mapped[str] = mapped_column(Text, default="")
    bili_jct: Mapped[str] = mapped_column(String(256), default="")
    buvid3: Mapped[str] = mapped_column(String(256), default="")
    buvid4: Mapped[str] = mapped_column(String(256), default="")
    dedeuserid: Mapped[str] = mapped_column(String(64), default="")
    ac_time_value: Mapped[str] = mapped_column(String(256), default="")
    # Full cookie jar captured at login. The individual fields above are kept
    # for compatibility and quick display; this preserves extra cookies such
    # as buvid_fp, b_nut, sid and bili_ticket.
    cookie_json: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    mid: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BiliVideo(Base):
    __tablename__ = "bili_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    bvid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    author: Mapped[str] = mapped_column(String(160), default="")
    url: Mapped[str] = mapped_column(String(300), default="")
    duration: Mapped[str] = mapped_column(String(32), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BiliAutopilotConfig(Base):
    __tablename__ = "bili_autopilot_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    like_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    favorite_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reply_comments_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    use_agent_context: Mapped[bool] = mapped_column(Boolean, default=True)
    forward_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forward_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asr_model: Mapped[str] = mapped_column(String(32), default="small")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    type: Mapped[str] = mapped_column(String(24), default="tool")
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(512), default="")
    risk: Mapped[str] = mapped_column(String(16), default="medium")
    enabled_assistant: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled_group: Mapped[bool] = mapped_column(Boolean, default=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_session_id", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), default="main", index=True)
    role: Mapped[str] = mapped_column(String(24), default="user")
    kind: Mapped[str] = mapped_column(String(24), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    media_path: Mapped[str] = mapped_column(String(512), default="")
    tool_name: Mapped[str] = mapped_column(String(64), default="")
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AssistantToolTask(Base):
    """A tool call made by the private-chat/Operator DSH agent."""

    __tablename__ = "assistant_tool_tasks"
    __table_args__ = (
        Index("ix_assistant_tool_tasks_session_status", "session_id", "status"),
        Index("ix_assistant_tool_tasks_call", "call_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), default="main", index=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    channel: Mapped[str] = mapped_column(String(24), default="dashboard")
    call_id: Mapped[str] = mapped_column(String(96), default="")
    tool_name: Mapped[str] = mapped_column(String(96), default="")
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_group_importance", "group_id", "importance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    scope: Mapped[str] = mapped_column(String(24), default="group")
    category: Mapped[str] = mapped_column(String(32), default="general")
    title: Mapped[str] = mapped_column(String(160), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=3)
    source: Mapped[str] = mapped_column(String(32), default="summary")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class UserImpression(Base):
    """Per-group emotional/relationship memory for one interacting member."""

    __tablename__ = "user_impressions"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "user_wxid", "account_key", name="uq_user_impression"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    user_wxid: Mapped[str] = mapped_column(String(128), default="")
    display_name: Mapped[str] = mapped_column(String(160), default="")
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    last_emotion: Mapped[str] = mapped_column(String(32), default="neutral")
    emotion_intensity: Mapped[float] = mapped_column(Float, default=0.0)
    emotion_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    traits: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ChatTask(Base):
    """A background group-chat task created by the intent router."""

    __tablename__ = "chat_tasks"
    __table_args__ = (
        Index("ix_chat_tasks_group_status", "group_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_wxid: Mapped[str] = mapped_column(String(128), default="")
    user_name: Mapped[str] = mapped_column(String(160), default="")
    intent: Mapped[str] = mapped_column(String(32), default="task")
    query: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    result: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AgentLog(Base):
    __tablename__ = "agent_logs"
    __table_args__ = (Index("ix_agent_logs_group_id_id", "group_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="info")
    event: Mapped[str] = mapped_column(String(32), default="info")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LLMUsage(Base):
    """One row per LLM request, used to measure prompt-cache hit rate.

    Providers report ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
    (OpenAI-compatible providers use ``prompt_tokens_details.cached_tokens``).
    Keeping the numbers locally makes prompt regressions visible immediately
    instead of waiting for the next provider invoice.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_site_id", "call_site", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_site: Mapped[str] = mapped_column(String(64), default="", index=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_prefix_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
