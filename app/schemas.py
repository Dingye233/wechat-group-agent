from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LoginRequest(BaseModel):
    adapter: str = "mock"


class SendMessageRequest(BaseModel):
    text: str = ""
    sticker_id: int | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> SendMessageRequest:
        if not self.text.strip() and self.sticker_id is None:
            raise ValueError("请输入文字或选择表情包")
        if len(self.text) > 2000:
            raise ValueError("文字不能超过 2000 字")
        return self


class GroupConfigUpdate(BaseModel):
    read_enabled: bool | None = None
    read_interval_seconds: int | None = Field(default=None, ge=0, le=3600)
    agent_enabled: bool | None = None
    trigger_mode: Literal["mention", "keyword", "all"] | None = None
    keywords: list[str] | None = None
    probability: float | None = Field(default=None, ge=0, le=1)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=3600)
    max_replies_per_hour: int | None = Field(default=None, ge=1, le=500)
    persona: str | None = None
    model: str | None = None
    sticker_enabled: bool | None = None
    multi_message_enabled: bool | None = None
    max_messages: int | None = Field(default=None, ge=2, le=6)
    proactive_enabled: bool | None = None
    proactive_interval_minutes: int | None = Field(default=None, ge=10, le=1440)
    quiet_start: str | None = None
    quiet_end: str | None = None
    mention_on_reply: bool | None = None

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = []
        for item in value:
            item = str(item).strip()
            if item and item not in cleaned:
                cleaned.append(item[:50])
        return cleaned[:30]

    @field_validator("persona")
    @classmethod
    def _persona(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4000:
            raise ValueError("人设不能超过 4000 字")
        return value

    @field_validator("model")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 120:
            raise ValueError("模型名过长")
        return value

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _quiet(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not TIME_RE.match(value):
            raise ValueError("静默时间格式应为 HH:MM")
        return value

    @model_validator(mode="after")
    def _quiet_pair(self) -> GroupConfigUpdate:
        if (self.quiet_start or "") and not (self.quiet_end or ""):
            raise ValueError("请同时设置静默开始和结束时间")
        if (self.quiet_end or "") and not (self.quiet_start or ""):
            raise ValueError("请同时设置静默开始和结束时间")
        return self


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    clear_api_key: bool = False
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_max_tokens: int | None = Field(default=None, ge=64, le=2000)
    llm_json_mode: bool | None = None
    default_persona: str | None = Field(default=None, max_length=12000)
    operator_persona: str | None = Field(default=None, max_length=12000)
    operator_prompt_extra: str | None = Field(default=None, max_length=12000)
    operator_system_prompt: str | None = Field(default=None, max_length=12000)
    group_prompt_extra: str | None = Field(default=None, max_length=12000)
    group_system_prompt: str | None = Field(default=None, max_length=12000)
    memory_prompt_template: str | None = Field(default=None, max_length=12000)
    memory_always_include: bool | None = None
    default_trigger_mode: Literal["mention", "keyword", "all"] | None = None
    default_keywords: list[str] | None = None
    context_messages: int | None = Field(default=None, ge=4, le=50)
    reply_delay_seconds: float | None = Field(default=None, ge=0, le=10)
    reply_delay_random_seconds: float | None = Field(default=None, ge=0, le=30)
    reply_cooldown_jitter_seconds: float | None = Field(default=None, ge=0, le=300)
    reply_global_interval_seconds: float | None = Field(default=None, ge=0, le=300)
    read_poll_interval_seconds: float | None = Field(default=None, ge=0.5, le=300)
    read_media_download_enabled: bool | None = None
    memory_enabled: bool | None = None
    memory_engine: Literal["auto", "mem0", "sqlite"] | None = None
    mem0_collection: str | None = Field(default=None, max_length=120)
    memory_search_timeout_seconds: float | None = Field(default=None, ge=0.3, le=8)
    fast_chat_planner: bool | None = None
    memory_forgetting_enabled: bool | None = None
    memory_forgetting_days: int | None = Field(default=None, ge=7, le=3650)
    memory_summary_max_input_chars: int | None = Field(default=None, ge=1000, le=30000)
    memory_summary_max_tokens: int | None = Field(default=None, ge=100, le=2000)
    memory_summary_max_items: int | None = Field(default=None, ge=1, le=12)
    memory_context_items: int | None = Field(default=None, ge=1, le=30)
    memory_context_max_chars: int | None = Field(default=None, ge=200, le=8000)
    assistant_workspace: str | None = None
    assistant_max_steps: int | None = Field(default=None, ge=0, le=500)
    assistant_max_tokens: int | None = Field(default=None, ge=128, le=3000)
    assistant_shell_timeout: int | None = Field(default=None, ge=5, le=600)
    opencode_enabled: bool | None = None
    group_chat_engine: Literal["dsh", "legacy"] | None = None
    group_channel_mode: Literal["dsh_primary", "legacy"] | None = None
    operator_engine: Literal["opencode", "dsh"] | None = None
    dsh_enabled: bool | None = None
    dsh_channel: Literal["stable", "alpha"] | None = None
    tray_close_action: Literal["tray", "exit"] | None = None
    takeover_hotkey: str | None = Field(default=None, max_length=40)
    auto_retry_failed_sends: bool | None = None
    timezone: str | None = None
    bot_aliases: str | None = None
    wcf_wechat_exe: str | None = None
    replica_wechat_exe: str | None = None
    replica_db_dir: str | None = None
    auto_login: Literal["", "mock", "replica", "wcf"] | None = None
    seed_sample_stickers: bool | None = None

    @field_validator(
        "default_persona",
        "operator_persona",
        "operator_prompt_extra",
        "operator_system_prompt",
        "group_prompt_extra",
        "group_system_prompt",
        "memory_prompt_template",
    )
    @classmethod
    def _prompt_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 12000:
            raise ValueError("提示词不能超过 12000 字")
        return value


class StickerUpdate(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    description: str | None = None


class StickerSendRequest(BaseModel):
    group_id: int
