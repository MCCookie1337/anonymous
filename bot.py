import asyncio
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, FSInputFile

logging.basicConfig(level=logging.INFO)

# ----------------- Конфиг -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Укажи токен бота в переменных окружения или впиши его в код.")

VIDEO_URL = os.getenv("VIDEO_URL")                 # опционально: прямая https-ссылка на mp4
VIDEO_PATH = os.getenv("VIDEO_PATH", "video.mp4")  # локальный файл рядом с bot.py

# ----------------- Инициализация -----------------
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Последовательная обработка сообщений пользователя (без «проскоков»)
_locks: Dict[int, asyncio.Lock] = {}
def user_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock

# ----------------- Данные квиза -----------------
@dataclass
class QA:
    question: str
    answers: List[str]  # допустимые строки (в нижнем регистре / точное совпадение)

QUESTIONS: List[QA] = [
    QA("Какой сейчас год?", ["2025"]),
    QA("Какое время года?", ["осень"]),
    QA("Какой день недели?", ["суббота"]),
    QA("Какой месяц?", ["ноябрь"]),
    QA("Сколько тебе лет?", ["24"]),
]

FINAL_CODE_MESSAGE = "Вот твой код от замка 3412"
INTERMEDIATE_SECRET = "238141264816"   # после него отправляем "Ребус"
FINAL_SECRET = "hello from moscow"     # после него отправляем видео

class Flow(StatesGroup):
    quiz = State()           # этап вопросов
    waiting_code = State()   # ждём "238141264816" с особыми ответами на «не тот» ввод
    waiting_final = State()  # после "Ребус": ждём "hello from moscow"

def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

# ----------------- Утилиты -----------------
async def send_video(m: Message):
    if VIDEO_URL:  # по ссылке
        await m.answer_video(VIDEO_URL)
        return
    path = pathlib.Path(VIDEO_PATH).resolve()  # локальный файл
    if not path.exists() or not path.is_file():
        await m.answer(f"Видео не найдено: {path.name}. Добавь файл рядом с bot.py или задай VIDEO_URL.")
        return
    await m.answer_video(FSInputFile(path))

async def send_question(m: Message, idx: int):
    await m.answer(QUESTIONS[idx].question)

# ----------------- Хэндлеры -----------------
@router.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        await state.clear()
        await state.update_data(idx=0)
        await m.answer("Давай поиграем в игру")      # 1-е сообщение
        await m.answer(QUESTIONS[0].question)        # 2-е сообщение: "Какой сейчас год?"
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
        txt_raw = m.text  # исходная строка
        txt = norm(txt_raw)

        # 1) Корректный код → "Ребус" и переход к финальному этапу
        if txt == INTERMEDIATE_SECRET:
            await m.answer("Ребус")
            await state.set_state(Flow.waiting_final)
            return

        # 2) Строка состоит ТОЛЬКО из 1 и 0 (без разделителей)
        if re.fullmatch(r"[01]+", txt_raw.strip()):
            await m.answer("Вот ты понимаешь что это за числа, вот и я нет, давай ка подумай хорошенько")
            return

        # 3) Строка состоит из чисел, разделённых пробелом/точкой/тире (микс разрешён)
        #    Примеры: "12 34", "12-34-56", "1.0.1", "10 - 11 . 12"
        if re.fullmatch(r"\s*\d+(?:[.\-\s]+\d+)+\s*", txt_raw):
            await m.answer("Ответ не в том формате")
            return

        # 4) Всё остальное
        await m.answer("Что тебе еще надо, достал уже")

@router.message(Flow.waiting_code)
async def on_waiting_code_non_text(m: Message):
    # Нечитаемые типы на этом шаге считаем «остальным вводом»
    await m.answer("Что тебе еще надо, достал уже")

@router.message(Flow.waiting_final, F.text)
async def on_waiting_final(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        if norm(m.text) == FINAL_SECRET:
            await send_video(m)
            await state.clear()
        else:
            await m.answer("Мне это не интересно")

@router.message(Flow.waiting_final)
async def on_waiting_final_non_text(m: Message):
    await m.answer("Мне это не интересно")

# Fallback — только вне любого состояния
@router.message(StateFilter(None))
async def fallback(m: Message):
    await m.answer("Набери /start чтобы начать игру заново.")

# ----------------- Запуск POLLING -----------------
async def main():
    await bot.delete_webhook(drop_pending_updates=True)  # на всякий случай
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
