"""FAQ-фичи: набор частых вопросов-кнопок, динамический top-5 по популярности,
кэш ответов в БД (для мгновенного ответа) и логирование вопросов."""

import logging

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from app import rag
from app.db import Feedback, FaqStat, QuestionLog, SessionLocal

# Предопределённые частые вопросы. label — текст кнопки, question — что уходит в RAG.
FAQ_ITEMS = [
    {"key": "about",     "label": "❓ Кто мы?",            "question": "Чем занимается компания и кто вы?"},
    {"key": "offer",     "label": "🎨 Что предлагаем?",   "question": "Какие товары и услуги вы предлагаете?"},
    {"key": "delivery",  "label": "🚚 Доставка и оплата", "question": "Как происходит доставка и какие есть способы оплаты?"},
    {"key": "office",    "label": "📍 Где офис?",          "question": "Где находится офис и какие у вас контакты?"},
    {"key": "designers", "label": "🖌 Для дизайнеров",     "question": "Что вы предлагаете дизайнерам и есть ли программа лояльности?"},
]
FAQ_BY_KEY = {item["key"]: item for item in FAQ_ITEMS}


def get_top_faq(limit: int = 5) -> list[dict]:
    """Возвращает FAQ, отсортированные по числу нажатий (most frequent сверху)."""
    with SessionLocal() as session:
        hits = {row.key: row.hits for row in session.execute(select(FaqStat)).scalars()}
    ordered = sorted(FAQ_ITEMS, key=lambda item: hits.get(item["key"], 0), reverse=True)
    return ordered[:limit]


def build_faq_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    """Строит инлайн-клавиатуру из переданных FAQ-пунктов (по одной кнопке в ряд)."""
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(text=item["label"], callback_data=f"faq:{item['key']}")
    builder.adjust(1)
    return builder.as_markup()


def get_faq_answer(key: str) -> str:
    """Возвращает ответ на FAQ-кнопку. +1 к счётчику нажатий. Берёт ответ из кэша,
    а если кэша нет — считает через RAG и кэширует (последующие тапы мгновенные)."""
    item = FAQ_BY_KEY[key]
    with SessionLocal() as session:
        stat = session.get(FaqStat, key)
        if stat is None:
            stat = FaqStat(key=key, hits=0)
            session.add(stat)
        stat.hits += 1

        if not stat.answer:
            stat.answer = rag.answer(item["question"])
        answer = stat.answer
        session.commit()
    return answer


def warm_faq_cache() -> None:
    """Фоновый прогрев: заранее считает и кэширует ответы на все FAQ при старте бота.
    Заодно «разогревает» соединение с Gemini — первый свободный вопрос отвечается быстрее."""
    for item in FAQ_ITEMS:
        try:
            with SessionLocal() as session:
                stat = session.get(FaqStat, item["key"])
                if stat and stat.answer:
                    continue  # уже в кэше
            answer = rag.answer(item["question"])
            with SessionLocal() as session:
                stat = session.get(FaqStat, item["key"])
                if stat is None:
                    stat = FaqStat(key=item["key"], hits=0)
                    session.add(stat)
                stat.answer = answer
                session.commit()
        except Exception:  # noqa: BLE001 — прогрев не должен ронять бота
            logging.exception("Не удалось прогреть FAQ-кэш для %s", item["key"])
    logging.info("FAQ-кэш прогрет.")


def log_question(chat_id: int, text: str) -> None:
    """Сохраняет свободный вопрос пользователя для аналитики частотности."""
    try:
        with SessionLocal() as session:
            session.add(QuestionLog(chat_id=chat_id, text=text))
            session.commit()
    except Exception:  # noqa: BLE001
        logging.exception("Не удалось залогировать вопрос")


def get_top_questions(limit: int = 10) -> list[tuple[str, int]]:
    """Топ свободных вопросов из лога по числу повторений — для команды /top."""
    with SessionLocal() as session:
        rows = session.execute(
            select(QuestionLog.text, func.count().label("cnt"))
            .group_by(QuestionLog.text)
            .order_by(func.count().desc())
            .limit(limit)
        ).all()
    return [(text, cnt) for text, cnt in rows]


def create_feedback(chat_id: int, question: str, answer: str) -> int:
    """Создаёт запись фидбэка (без оценки) и возвращает её id для callback-кнопок."""
    with SessionLocal() as session:
        feedback = Feedback(chat_id=chat_id, question=question, answer=answer)
        session.add(feedback)
        session.commit()
        return feedback.id


def set_rating(feedback_id: int, rating: int) -> None:
    """Проставляет оценку (1 / -1) к ранее созданному фидбэку."""
    with SessionLocal() as session:
        feedback = session.get(Feedback, feedback_id)
        if feedback is not None:
            feedback.rating = rating
            session.commit()
