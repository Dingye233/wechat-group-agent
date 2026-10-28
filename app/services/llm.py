from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.services.llm_usage import record_usage

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cache_hit_tokens(usage: dict[str, Any], prompt_tokens: int) -> int:
    """Read the provider's prompt-cache hit count.

    DeepSeek exposes ``prompt_cache_hit_tokens``; OpenAI-compatible gateways
    usually expose ``prompt_tokens_details.cached_tokens``. Both are supported
    so the dashboard keeps working if the provider changes.
    """
    details = usage.get("prompt_tokens_details")
    input_details = usage.get("input_tokens_details")
    candidates = [
        usage.get("prompt_cache_hit_tokens"),
        usage.get("cache_hit_tokens"),
        details.get("cached_tokens") if isinstance(details, dict) else None,
        (
            input_details.get("cached_tokens")
            if isinstance(input_details, dict)
            else None
        ),
    ]
    for value in candidates:
        hit = _as_int(value)
        if hit > 0:
            return min(hit, prompt_tokens) if prompt_tokens else hit
    return 0


def cache_miss_tokens(
    usage: dict[str, Any], prompt_tokens: int, hit_tokens: int
) -> int:
    miss = _as_int(usage.get("prompt_cache_miss_tokens"))
    if miss > 0:
        return miss
    return max(prompt_tokens - hit_tokens, 0)


class LLMService:
    """Minimal OpenAI-compatible chat completion client.

    Works with DeepSeek, OpenAI, Moonshot, Qwen, Ollama and most providers that
    expose ``/chat/completions``. No vendor SDK is required.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(75.0, connect=12.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.8,
        max_tokens: int = 300,
        json_mode: bool = True,
        call_site: str = "generic",
        cache_prefix_chars: int = 0,
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not api_key:
            raise LLMError("未配置 API Key")
        if not model:
            raise LLMError("未配置模型名称")

        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages
            or [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()

        def report_usage(
            *,
            prompt_tokens: int = 0,
            completion_tokens: int = 0,
            usage: dict[str, Any] | None = None,
            ok: bool,
        ) -> dict[str, int]:
            usage = usage or {}
            prompt = prompt_tokens or _as_int(usage.get("prompt_tokens"))
            hit = cache_hit_tokens(usage, prompt) if usage else 0
            miss = cache_miss_tokens(usage, prompt, hit) if usage else 0
            latency = int((time.perf_counter() - started) * 1000)
            record_usage(
                call_site=call_site,
                model=model,
                prompt_tokens=prompt,
                completion_tokens=completion_tokens
                or _as_int(usage.get("completion_tokens")),
                cache_hit_tokens=hit,
                cache_miss_tokens=miss,
                latency_ms=latency,
                cache_prefix_chars=cache_prefix_chars,
                ok=ok,
            )
            if prompt:
                logger.debug(
                    "LLM %s model=%s prompt=%d cache_hit=%d (%.0f%%) latency=%dms",
                    call_site,
                    model,
                    prompt,
                    hit,
                    (hit / prompt) * 100,
                    latency,
                )
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion_tokens,
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "cache_hit_rate": round(hit / prompt, 4) if prompt else 0.0,
            }

        try:
            response = await self._client.post(url, json=payload, headers=headers)
            if response.status_code in (400, 422) and json_mode:
                # Some compatible providers do not support response_format.
                payload.pop("response_format", None)
                response = await self._client.post(
                    url, json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            report_usage(ok=False)
            raise LLMError("模型响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            report_usage(ok=False)
            raise LLMError(f"无法连接模型服务：{exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            report_usage(ok=False)
            detail = self._short_error(response)
            raise LLMError(self._friendly_error(response.status_code, detail))

        try:
            data = response.json()
        except ValueError as exc:
            report_usage(ok=False)
            raise LLMError("模型服务返回了非 JSON 响应") from exc

        choices = data.get("choices") or []
        if not choices:
            report_usage(ok=False)
            raise LLMError("模型没有返回候选结果")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content = str(content or "").strip()
        usage = data.get("usage") or {}
        stats = report_usage(usage=usage, ok=bool(content))
        if not content:
            raise LLMError("模型返回了空内容")

        return {
            "content": content,
            "model": data.get("model") or model,
            "latency_ms": latency_ms,
            "prompt_tokens": stats["prompt_tokens"],
            "completion_tokens": stats["completion_tokens"],
            "total_tokens": stats["prompt_tokens"] + stats["completion_tokens"],
            "cache_hit_tokens": stats["cache_hit_tokens"],
            "cache_miss_tokens": stats["cache_miss_tokens"],
            "cache_hit_rate": stats["cache_hit_rate"],
        }

    async def complete_json(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.complete(**kwargs)
        parsed = parse_json_object(result["content"])
        if parsed is None:
            raise LLMError("模型未按 JSON 格式返回，请重试或更换模型")
        return parsed, result

    async def test(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
    ) -> dict[str, Any]:
        result = await self.complete(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt="你是 API 连通性测试助手。",
            user_prompt="请只回复四个字：连接成功",
            temperature=0.1,
            max_tokens=32,
            json_mode=False,
            call_site="settings.test",
        )
        return {
            "ok": True,
            "reply": result["content"][:100],
            "model": result["model"],
            "latency_ms": result["latency_ms"],
        }

    @staticmethod
    def _short_error(response: httpx.Response) -> str:
        text = response.text.replace("\n", " ").strip()
        return text[:300]

    @staticmethod
    def _friendly_error(status: int, detail: str) -> str:
        if status == 401:
            return "API Key 无效或已过期"
        if status == 403:
            return "没有权限访问该模型"
        if status == 404:
            return "接口地址或模型不存在"
        if status == 429:
            return "请求过于频繁或额度不足"
        if status >= 500:
            return "模型服务暂时不可用"
        return f"模型请求失败（HTTP {status}）：{detail}"


def _first_json_object(text: str) -> str | None:
    """Return the first balanced {...} block, ignoring trailing junk."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_json_object(content: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM response.

    Models occasionally append an extra ``}`` or a short sentence after the
    JSON object. Use a string-aware brace scanner instead of ``rfind("}")`` so
    those responses still parse and the raw protocol never leaks to the user.
    """
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text[3:].strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    candidates = [text]
    first = _first_json_object(text)
    if first and first != text:
        candidates.append(first)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        return value if isinstance(value, dict) else None
    return None
