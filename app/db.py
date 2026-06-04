"""Слой работы с БД: движок SQLAlchemy, модель чанка с векторным полем pgvector."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from pgvector.sqlalchemy import Vector

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


class DocumentChunk(Base):
    """Один фрагмент базы знаний компании вместе с его эмбеддингом."""

    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    source = Column(String(255))                       # из какого файла взят чанк
    content = Column(Text, nullable=False)             # текст фрагмента
    embedding = Column(Vector(settings.embedding_dim)) # векторное представление


class FaqStat(Base):
    """Статистика и кэш ответов по кнопкам FAQ.

    hits   — сколько раз нажимали кнопку (для сортировки «most frequent»).
    answer — закэшированный ответ, чтобы тап по кнопке отвечал мгновенно без вызова LLM.
    """

    __tablename__ = "faq_stats"

    key = Column(String(64), primary_key=True)
    hits = Column(Integer, nullable=False, default=0)
    answer = Column(Text)  # NULL, пока не прогрет


class QuestionLog(Base):
    """Лог свободных вопросов пользователей — основа аналитики частотности."""

    __tablename__ = "question_log"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    """Оценка ответа пользователем (кнопки 👍/👎) — петля контроля качества."""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    question = Column(Text)
    answer = Column(Text)
    rating = Column(Integer)  # 1 (👍), -1 (👎) или NULL (ещё не оценено)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    """Создаёт таблицы. Расширение pgvector включается через db/init.sql при старте Postgres."""
    Base.metadata.create_all(engine)
