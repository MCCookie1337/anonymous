import asyncio
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List

from aiohttp import web  # мини-вебсервер для Render (keep-alive)
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.types import FSInputFile  # ВАЖНО: для отправки локального файла в aiogram v3

logging.basicConfig(level=logging.INFO)

# ---------- Переменные окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Укажи токен бота в переменных окружения.")

VIDEO_URL = os.getenv("VIDEO_URL")              # опционально: прямая https-ссылка на mp4
VIDEO_PATH = os.getenv("VIDEO_PATH", "video.mp4")  # локальный файл в репозитории

# ---------- Инициализация бота/диспетчера ----------
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ---------- Пер-пользовательские блокировки (чтобы не было гонок) ----------
_locks: Dict[int, asyncio.Lock] = {}
def user_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock

# ---------- Данные квиза ----------
@dataclass
class QA:
    question: str
    answers: List[str]  # допустимые ответы (в нижнем регистре/точное совпадение)

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
    quiz = State()
    waiting_secret = State()

def norm(t: str) -> str:
    t = t.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t

# ---------- Отправка видео ----------
async def send_video(m: Message):
    # 1) Если задана ссылка — шлём URL
    if VIDEO_URL:
        await m.answer_video(VIDEO_URL)
        return

    # 2) Иначе пробуем локальный файл
    path = pathlib.Path(VIDEO_PATH).resolve()
    if not path.exists() or not path.is_file():
        await m.answer(f"Видео не найдено: {path.name}. Добавь файл рядом с bot.py или задай VIDEO_URL.")
        return

    # ВАЖНО: для aiogram v3 нужен FSInputFile
    await m.answer_video(FSInputFile(path))

async def send_question(m: Message, idx: int):
    await m.answer(QUESTIONS[idx].question)

# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        await state.clear()
        await state.update_data(idx=0)
        await m.answer("Давай поиграем в игру")           # первое сообщение
        await m.answer(QUESTIONS[0].question)             # второе сообщение: "Какой сейчас год?"
        await state.set_state(Flow.quiz)

@router.message(Flow.quiz, F.text)
async def on_quiz_answer(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        data = await state.get_data()
        idx = int(data.get("idx", 0))
        qa = QUESTIONS[idx]

        if norm(m.text) in qa.answers:
            idx += 1
            if idx >= len(QUESTIONS):
                # Все ответы даны верно → выдаём код и ждём секретную фразу
                await m.answer(FINAL_CODE)
                await state.set_state(Flow.waiting_secret)
            else:
                await state.update_data(idx=idx)
                await m.answer(QUESTIONS[idx].question)
        else:
            await m.answer("Ответ неверный")
            await send_question(m, idx)

@router.message(Flow.waiting_secret, F.text)
async def on_secret(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        if norm(m.text) == SECRET_PHRASE:
            await send_video(m)
            await state.clear()
        else:
            await m.answer("Мне это не интересно")

# Fallback сработает ТОЛЬКО если пользователь вне любого состояния
@router.message(StateFilter(None))
async def fallback(m: Message):
    await m.answer("Набери /start чтобы начать игру заново.")

# ---------- Запуск: polling + мини-вебсервер (для Render) ----------
async def run_polling():
    # если раньше стоял webhook — снимаем, иначе polling вернёт 409
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

async def healthz(_request):
    return web.Response(text="ok")

async def run_web():
    app = web.Application()
    app.router.add_get("/", healthz)
    app.router.add_get("/healthz", healthz)

    # Render передаёт порт в переменной окружения PORT
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health server started on :{port}")

    # держим задачу живой
    while True:
        await asyncio.sleep(3600)

async def main():
    # Запускаем вебсервер и polling параллельно
    await asyncio.gather(run_web(), run_polling())

if __name__ == "__main__":
    asyncio.run(main())
