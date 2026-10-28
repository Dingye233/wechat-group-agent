from __future__ import annotations

from fastapi import Request

from app.core.runtime import RuntimeManager


def get_runtime(request: Request) -> RuntimeManager:
    return request.app.state.runtime
