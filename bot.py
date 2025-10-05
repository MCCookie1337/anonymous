import asyncio, os, re, logging, pathlib
from dataclasses import dataclass
from typing import List
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

# Настройки видео: либо укажи VIDEO_URL, либо положи файл video.mp4 в корень проекта
VIDEO_URL = os.getenv("VIDEO_URL")      # например: https://example.com/clip.mp4
VIDEO_PATH = os.getenv("VIDEO_PATH", "video.mp4")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================== ЛОГИКА И ДАННЫЕ ==================
@dataclass
class QA:
    question: str
    answers: List[str]  # допустимые ответы (в нижнем регистре)

# Ровно твой сценарий, по шагам:
QUESTIONS: List[QA] = [
    QA("Какой сейчас год?", ["2025"]),
    QA("Какое время года?", ["осень"]),
    QA("Какой день недели?", ["суббота"]),
    QA("Какой месяц?", ["ноябрь"]),
    QA("Сколько тебе лет?", ["24"]),
]

FINAL_CODE = "3412"
SECRET_PHRASE = "hello from moscow"

class Flow(StatesGroup):
    quiz = State()            # отвечаем на вопросы
    waiting_secret = State()  # ждём кодовую фразу

def norm(text: str) -> str:
    # нормализация ответа: нижний регистр + лишние пробелы
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t

async def send_video(m: Message):
    # 1) Если указана VIDEO_URL — отправляем по ссылке
    if VIDEO_URL:
        await m.answer_video(VIDEO_URL)
        return
    # 2) Иначе пытаемся отправить локальный файл video.mp4
    path = pathlib.Path(VIDEO_PATH)
    if path.exists() and path.is_file():
        with path.open("rb") as f:
            await m.answer_video(f)
        return
    # 3) Видео не настроено
    await m.answer("Видео не настроено. Добавь переменную VIDEO_URL или файл video.mp4.")

async def send_question(m: Message, idx: int):
    await m.answer(QUESTIONS[idx].question)

# ================== ХЭНДЛЕРЫ ==================
@router.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    # Сбрасываем состояние и начинаем заново
    await state.clear()
    await state.update_data(idx=0)
    await m.answer("Давай поиграем в игру")          # первое сообщение
    await m.answer(QUESTIONS[0].question)            # второе: "Какой сейчас год?"
    await state.set_state(Flow.quiz)

@router.message(Flow.quiz, F.text)
async def on_quiz_answer(m: Message, state: FSMContext):
    data = await state.get_data()
    idx = int(data.get("idx", 0))
    qa = QUESTIONS[idx]
    user = norm(m.text)

    if user in qa.answers:
        # Верно → либо следующий вопрос, либо финал
        idx += 1
        if idx >= len(QUESTIONS):
            # Все 5 ответов верны → отправляем код и ждём секретную фразу
            await m.answer(FINAL_CODE)
            await state.set_state(Flow.waiting_secret)
            return
        else:
            await state.update_data(idx=idx)
            # Задаём следующий вопрос
            await m.answer(QUESTIONS[idx].question)
    else:
        # Неверно → сообщаем и повторяем тот же вопрос
        await m.answer("Ответ неверный")
        await send_question(m, idx)

@router.message(Flow.quiz)
async def on_quiz_non_text(m: Message):
    await m.answer("Пришли, пожалуйста, текстовый ответ.")

@router.message(Flow.waiting_secret, F.text)
async def on_secret(m: Message, state: FSMContext):
    if norm(m.text) == SECRET_PHRASE:
        await send_video(m)
        # Можно закончить сценарий или остаться в этом же состоянии.
        await state.clear()
    else:
        await m.answer("Мне это не интересно")

@router.message(Flow.waiting_secret)
async def on_secret_non_text(m: Message):
    await m.answer("Мне это не интересно")

@router.message()
async def fallback(m: Message):
    await m.answer("Набери /start, чтобы начать игру заново.")

# ================== ЗАПУСК POLLING ==================
async def main():
    # На всякий случай отключаем webhook (если когда-то был включён)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
