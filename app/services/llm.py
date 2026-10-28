from __future__ import annotations

import json
import time
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


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
            "messages": [
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
        try:
            response = await self._client.post(url, json=payload, headers=headers)
            if response.status_code in (400, 422) and json_mode:
                # Some compatible providers do not support response_format.
                payload.pop("response_format", None)
                response = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError("模型响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"无法连接模型服务：{exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            detail = self._short_error(response)
            raise LLMError(self._friendly_error(response.status_code, detail))

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("模型服务返回了非 JSON 响应") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型没有返回候选结果")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content = str(content or "").strip()
        if not content:
            raise LLMError("模型返回了空内容")

        usage = data.get("usage") or {}
        return {
            "content": content,
            "model": data.get("model") or model,
            "latency_ms": latency_ms,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
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
