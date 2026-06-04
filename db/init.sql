-- Включаем расширение pgvector. Выполняется автоматически при первом старте контейнера
-- Postgres (файл смонтирован в /docker-entrypoint-initdb.d/). Таблицы создаёт SQLAlchemy.
CREATE EXTENSION IF NOT EXISTS vector;
