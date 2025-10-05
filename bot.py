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
from aiogram.types import Message, FSInputFile

logging.basicConfig(level=logging.INFO)

# ---------- Переменные окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    # Для локального теста можно временно вписать токен сюда строкой
    raise RuntimeError("BOT_TOKEN не задан. Укажи токен бота в переменных окружения.")

VIDEO_URL = os.getenv("VIDEO_URL")                     # опционально: прямая https-ссылка на mp4
VIDEO_PATH = os.getenv("VIDEO_PATH", "video.mp4")      # локальный файл в репозитории

# ---------- Инициализация ----------
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ---------- Пер-пользовательские блокировки (устраняют гонки) ----------
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
    answers: List[str]  # допустимые строки в НИЖНЕМ регистре

QUESTIONS: List[QA] = [
    QA("Какой сейчас год?", ["2025"]),
    QA("Какое время года?", ["осень"]),
    QA("Какой день недели?", ["суббота"]),
    QA("Какой месяц?", ["ноябрь"]),
    QA("Сколько тебе лет?", ["24"]),
]

FINAL_CODE_MESSAGE = "Вот твой код от замка 3412"
INTERMEDIATE_SECRET = "238141264816"        # после него отправляем "Ребус"
FINAL_SECRET = "hello from moscow"          # после него отправляем видео

class Flow(StatesGroup):
    quiz = State()              # этап вопросов
    waiting_code = State()      # ждём "238141264816"
    riddle = State()            # прислан "Ребус", обрабатываем особые ответы

def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

# ---------- Утилиты ----------
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
    await m.answer_video(FSInputFile(path))

async def send_question(m: Message, idx: int):
    await m.answer(QUESTIONS[idx].question)

# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        await state.clear()
        await state.update_data(idx=0)
        await m.answer("Давай поиграем в игру")
        await m.answer(QUESTIONS[0].question)
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
                # Все 5 ответов верны -> отправляем кодовое сообщение и переходим к ожиданию фразы "238141264816"
                await m.answer(FINAL_CODE_MESSAGE)
                await state.set_state(Flow.waiting_code)
            else:
                await state.update_data(idx=idx)
                await m.answer(QUESTIONS[idx].question)
        else:
            await m.answer("Ответ неверный")
            await send_question(m, idx)

@router.message(Flow.waiting_code, F.text)
async def on_waiting_code(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        if norm(m.text) == INTERMEDIATE_SECRET:
            # Верная кодовая фраза -> шлём "Ребус" и переходим в этап ребуса
            await m.answer("Ребус")
            await state.set_state(Flow.riddle)
        else:
            await m.answer("Код неверный")

@router.message(Flow.waiting_code)
async def on_waiting_code_non_text(m: Message):
    await m.answer("Код неверный")

@router.message(Flow.riddle, F.text)
async def on_riddle(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        txt = m.text.strip()

        # Сначала проверяем финальную кодовую фразу — она должна переводить к видео
        if norm(txt) == FINAL_SECRET:
            await send_video(m)
            await state.clear()
            return

        # 1) Только из 1 и 0 (без пробелов и знаков)
        if re.fullmatch(r"[01]+", txt):
            await m.answer("Вот ты понимаешь что это за числа, вот и я нет, давай ка подумай хорошенько")
            return

        # 2) Если строка состоит ТОЛЬКО из цифр, пробелов, точек или тире (любой комбинации) — «не тот формат»
        if re.fullmatch(r"[0-9\s\.\-]+", txt):
            await m.answer("Ответ не в том формате")
            return

        # 3) Любой другой ввод
        await m.answer("Что тебе еще надо, достал уже")

@router.message(Flow.riddle)
async def on_riddle_non_text(m: Message):
    await m.answer("Что тебе еще надо, достал уже")

# Fallback — только вне любого состояния
@router.message(StateFilter(None))
async def fallback(m: Message):
    await m.answer("Набери /start чтобы начать игру заново.")

# ---------- Запуск: polling + мини-вебсервер (для Render) ----------
async def run_polling():
    await bot.delete_webhook(drop_pending_updates=True)  # если раньше стоял webhook
    await dp.start_polling(bot)

async def healthz(_request):
    return web.Response(text="ok")

async def run_web():
    app = web.Application()
    app.router.add_get("/", healthz)
    app.router.add_get("/healthz", healthz)

    port = int(os.getenv("PORT", "10000"))  # Render прокидывает порт сюда
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health server started on :{port}")

    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(run_web(), run_polling())

if __name__ == "__main__":
    asyncio.run(main())
