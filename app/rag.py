"""RAG-ядро: эмбеддинги, векторный поиск в pgvector и генерация ответа через Gemini."""

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from sqlalchemy import select

from app.config import settings
from app.db import DocumentChunk, SessionLocal

embeddings = GoogleGenerativeAIEmbeddings(
    model=settings.embedding_model,
    google_api_key=settings.google_api_key,
)

llm = ChatGoogleGenerativeAI(
    model=settings.chat_model,
    google_api_key=settings.google_api_key,
    temperature=0.2,  # низкая температура -> меньше выдумок
)

SYSTEM_PROMPT = """Ты — вежливый ассистент интернет-магазина красок «Центр Красок #1».
Отвечай на вопросы клиентов ТОЛЬКО на основе приведённого ниже КОНТЕКСТА о компании.

Правила:
- Используй только факты из контекста. Ничего не выдумывай и не додумывай.
- Если в контексте нет ответа — честно скажи, что у тебя нет этой информации,
  и предложи обратиться по контактному телефону компании.
- Отвечай на том языке, на котором задан вопрос (русский или казахский).
- Отвечай кратко, по делу и дружелюбно. Цены и наличие конкретных товаров уточняй
  как «уточняйте у менеджера», если их нет в контексте.
- Не отвечай на вопросы, не связанные с компанией; мягко возвращай разговор к теме магазина.
- Пиши обычным текстом БЕЗ Markdown-разметки: никаких **, *, #, маркированных списков
  со звёздочками. Для перечислений используй маркер «•» или просто новые строки.

КОНТЕКСТ:
{context}
"""

FALLBACK = (
    "Извините, у меня нет точной информации по этому вопросу. "
    "Лучше уточнить у менеджера: +7 (777) 292-84-01 или info@centr-krasok.kz."
)

# Подчистка Markdown на случай, если модель всё же вставила разметку.
_MD_BOLD = re.compile(r"\*\*|__|`")
_MD_HEADER = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")
_MD_BULLET = re.compile(r"(?m)^([ \t]*)[*\-][ \t]+")


def strip_markdown(text: str) -> str:
    """Убирает Markdown-разметку, чтобы в Telegram не было «голых» ** и #."""
    text = _MD_BOLD.sub("", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BULLET.sub(r"\1• ", text)
    return text


def retrieve(question: str, k: int):
    """Возвращает список (текст_чанка, косинусное_расстояние), отсортированный по близости."""
    query_embedding = embeddings.embed_query(question)
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
    stmt = select(DocumentChunk.content, distance).order_by(distance).limit(k)
    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    return [(content, dist) for content, dist in rows]


def _build_messages(question: str, history: list[tuple[str, str]]) -> list | None:
    """Готовит сообщения для LLM. Возвращает None, если релевантного контекста не нашлось."""
    results = retrieve(question, settings.top_k)
    relevant = [(text, dist) for text, dist in results if dist <= settings.max_distance]
    if not relevant:
        return None

    context = "\n\n---\n\n".join(text for text, _ in relevant)
    messages: list = [SystemMessage(content=SYSTEM_PROMPT.format(context=context))]
    for role, text in history:
        messages.append(HumanMessage(content=text) if role == "human" else AIMessage(content=text))
    messages.append(HumanMessage(content=question))
    return messages


def answer(question: str, history: list[tuple[str, str]] | None = None) -> str:
    """Ищет контекст и генерирует ответ целиком. history — список (role, text)."""
    messages = _build_messages(question, history or [])
    if messages is None:  # нет релевантного контекста -> не зовём LLM
        return FALLBACK
    response = llm.invoke(messages)
    return strip_markdown(response.content.strip()) or FALLBACK


def answer_stream(question: str, history: list[tuple[str, str]] | None = None):
    """Генератор: отдаёт ответ кусками по мере генерации (для «печатающегося» ответа в боте)."""
    messages = _build_messages(question, history or [])
    if messages is None:
        yield FALLBACK
        return
    produced = False
    for chunk in llm.stream(messages):
        text = chunk.content
        if text:
            produced = True
            yield text
    if not produced:
        yield FALLBACK
