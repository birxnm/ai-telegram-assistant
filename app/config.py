"""Конфигурация приложения. Все значения читаются из переменных окружения / .env."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Секреты
    telegram_bot_token: str
    google_api_key: str

    # Подключение к БД (для docker-compose переопределяется на host=db)
    database_url: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/krasok"
    )

    # Модели Gemini
    chat_model: str = "gemini-2.5-flash"
    embedding_model: str = "models/gemini-embedding-001"
    embedding_dim: int = 3072  # размерность gemini-embedding-001 по умолчанию (нормализованы)

    # Параметры RAG
    top_k: int = 4              # сколько чанков извлекать
    max_distance: float = 0.6   # порог косинусного расстояния (фильтр нерелевантного)
    history_size: int = 6       # сколько последних реплик хранить в контексте диалога

    # Стриминг ответа: минимальный интервал между правками сообщения (сек), чтобы не упереться в лимиты Telegram
    stream_min_interval: float = 0.7

    # Антиспам: не более N сообщений за окно в секунд на один чат
    rate_limit_messages: int = 8
    rate_limit_window_seconds: int = 20

    # Размер пула потоков для параллельной обработки запросов к LLM (стриминг/ответы)
    worker_threads: int = 16

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        """Приводит URL к драйверу psycopg2 (хостинги отдают postgres:// или postgresql://)."""
        for prefix in ("postgresql+psycopg2://", "postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg2://" + value[len(prefix):]
        return value


settings = Settings()
