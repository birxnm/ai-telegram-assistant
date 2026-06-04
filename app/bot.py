"""Telegram-бот на aiogram. Чат-формат + продакшн-фичи.

Фичи:
- Стриминг ответа (текст «печатается» по мере генерации).
- Top-5 частых вопросов кнопками (сортировка по реальной популярности).
- Кэш ответов на FAQ -> мгновенный ответ по кнопке без вызова LLM.
- Оценка ответа 👍/👎 с сохранением в БД.
- Команда /top — аналитика самых частых вопросов.
- Антиспам (rate limiting) по каждому чату.
- Фоновый прогрев кэша при старте -> быстрее сразу после /start.

Запуск: python -m app.bot
"""

import asyncio
import logging
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import faq, rag
from app.config import settings
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()

# Память диалога в ОЗУ: на каждый чат — последние реплики (role, text).
histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=settings.history_size))
# Тайминги сообщений для антиспама.
_rate_log: dict[int, deque] = defaultdict(deque)

WELCOME = (
    "Здравствуйте! 👋 Я виртуальный ассистент магазина «Центр Красок #1».\n\n"
    "Спросите меня о товарах, услугах, доставке, оплате или контактах — "
    "просто напишите вопрос обычным сообщением.\n\n"
    "Или выберите частый вопрос ниже 👇"
)


# --- Вспомогательные ---------------------------------------------------------

def is_allowed(chat_id: int) -> bool:
    """Скользящее окно: не более N сообщений за window секунд на чат."""
    now = monotonic()
    window = settings.rate_limit_window_seconds
    log = _rate_log[chat_id]
    while log and now - log[0] > window:
        log.popleft()
    if len(log) >= settings.rate_limit_messages:
        return False
    log.append(now)
    return True


def build_answer_keyboard(feedback_id: int, faq_items: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура под ответом: ряд оценки 👍/👎 + список частых вопросов."""
    builder = InlineKeyboardBuilder()
    builder.button(text="👍", callback_data=f"fb:{feedback_id}:1")
    builder.button(text="👎", callback_data=f"fb:{feedback_id}:-1")
    for item in faq_items:
        builder.button(text=item["label"], callback_data=f"faq:{item['key']}")
    builder.adjust(2, *([1] * len(faq_items)))  # оценка в один ряд, FAQ по одному
    return builder.as_markup()


async def iter_in_thread(sync_gen_factory):
    """Мост: выполняет блокирующий генератор в отдельном потоке и отдаёт элементы в async."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def worker():
        try:
            for item in sync_gen_factory():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # noqa: BLE001 — пробросим в async-сторону
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, DONE)

    loop.run_in_executor(None, worker)
    while True:
        item = await queue.get()
        if item is DONE:
            return
        if isinstance(item, Exception):
            raise item
        yield item


async def finalize_answer(message_to_edit: Message, chat_id: int, question: str, text: str) -> None:
    """Сохраняет фидбэк-запись и прикрепляет к ответу клавиатуру (оценка + FAQ)."""
    feedback_id = await asyncio.to_thread(faq.create_feedback, chat_id, question, text)
    items = await asyncio.to_thread(faq.get_top_faq, 5)
    keyboard = build_answer_keyboard(feedback_id, items)
    try:
        await message_to_edit.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        # На случай, если контент не изменился или сообщение нельзя редактировать.
        await bot.send_message(chat_id, text, reply_markup=keyboard)


# --- Хендлеры ----------------------------------------------------------------

@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    histories.pop(message.chat.id, None)
    items = await asyncio.to_thread(faq.get_top_faq, 5)
    await message.answer(WELCOME, reply_markup=faq.build_faq_keyboard(items))


@dp.message(Command("top"))
async def on_top(message: Message) -> None:
    rows = await asyncio.to_thread(faq.get_top_questions, 10)
    if not rows:
        await message.answer("Пока нет данных по вопросам.")
        return
    lines = [f"{i}. {text} — {cnt}" for i, (text, cnt) in enumerate(rows, 1)]
    await message.answer("📊 Самые частые вопросы:\n\n" + "\n".join(lines))


@dp.callback_query(F.data.startswith("faq:"))
async def on_faq_button(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    item = faq.FAQ_BY_KEY.get(key)
    if item is None or callback.message is None:
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    if not is_allowed(chat_id):
        await callback.answer("⏳ Слишком много запросов. Подождите.", show_alert=False)
        return

    await callback.answer()  # убираем «часики»
    reply = await asyncio.to_thread(faq.get_faq_answer, key)  # из кэша -> мгновенно

    histories[chat_id].append(("human", item["question"]))
    histories[chat_id].append(("ai", reply))

    # Компактно: то же сообщение-меню превращается в ответ прямо на месте,
    # новые сообщения не плодятся.
    feedback_id = await asyncio.to_thread(faq.create_feedback, chat_id, item["question"], reply)
    items = await asyncio.to_thread(faq.get_top_faq, 5)
    keyboard = build_answer_keyboard(feedback_id, items)
    try:
        await callback.message.edit_text(reply, reply_markup=keyboard)
    except TelegramBadRequest:
        pass  # повторный тап по той же кнопке -> «message is not modified», игнорируем


@dp.callback_query(F.data.startswith("fb:"))
async def on_feedback(callback: CallbackQuery) -> None:
    try:
        _, raw_id, raw_rating = callback.data.split(":")
        await asyncio.to_thread(faq.set_rating, int(raw_id), int(raw_rating))
    except Exception:  # noqa: BLE001
        await callback.answer()
        return

    # После оценки убираем кнопки 👍/👎 (оставляем FAQ) — чище и без повторных голосований.
    if callback.message is not None:
        items = await asyncio.to_thread(faq.get_top_faq, 5)
        try:
            await callback.message.edit_reply_markup(reply_markup=faq.build_faq_keyboard(items))
        except TelegramBadRequest:
            pass
    await callback.answer("Спасибо за оценку! 🙌")


@dp.message(F.text)
async def on_text(message: Message) -> None:
    chat_id = message.chat.id
    if not is_allowed(chat_id):
        await message.answer("⏳ Слишком много запросов. Подождите немного.")
        return

    question = message.text
    await asyncio.to_thread(faq.log_question, chat_id, question)
    await bot.send_chat_action(chat_id, ChatAction.TYPING)

    history = list(histories[chat_id])
    placeholder = await message.answer("✍️ Печатаю…")

    buffer = ""
    last_edit = 0.0
    try:
        async for delta in iter_in_thread(lambda: rag.answer_stream(question, history)):
            buffer += delta
            now = monotonic()
            if buffer.strip() and now - last_edit >= settings.stream_min_interval:
                try:
                    await placeholder.edit_text(rag.strip_markdown(buffer))
                except TelegramBadRequest:
                    pass  # «message is not modified» / флуд — просто пропускаем правку
                last_edit = now
    except Exception:  # noqa: BLE001
        logging.exception("Ошибка при стриминге ответа")
        buffer = buffer or "Извините, произошла техническая ошибка. Попробуйте, пожалуйста, ещё раз."

    final = rag.strip_markdown(buffer.strip()) or rag.FALLBACK
    histories[chat_id].append(("human", question))
    histories[chat_id].append(("ai", final))
    await finalize_answer(placeholder, chat_id, question, final)


@dp.message()
async def on_other(message: Message) -> None:
    await message.answer("Я понимаю только текстовые сообщения. Напишите ваш вопрос текстом 🙂")


async def main() -> None:
    init_db()  # на случай локального запуска без отдельного ingest
    # Явный пул потоков: блокирующие вызовы LLM (стриминг/ответы) идут параллельно,
    # один «занятый» поток на запрос не тормозит остальных пользователей.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=settings.worker_threads))

    asyncio.create_task(asyncio.to_thread(faq.warm_faq_cache))  # фоновый прогрев кэша
    logging.info("Бот запускается (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
