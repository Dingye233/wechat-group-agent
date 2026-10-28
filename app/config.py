from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Process-level settings loaded from .env.

    Business settings that can be changed at runtime live in the database
    (see app/services/settings.py). This class only holds bootstrap values.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "群伴 · WeChat Group Agent"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = BASE_DIR / "data"
    frontend_dir: Path = BASE_DIR / "frontend"

    auto_login: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    seed_sample_stickers: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def sticker_dir(self) -> Path:
        return self.data_dir / "stickers"

    @property
    def chat_media_dir(self) -> Path:
        return self.data_dir / "media" / "chat"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.sticker_dir, self.chat_media_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
