import asyncio
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

logging.basicConfig(level=logging.INFO)

# ----------------- Конфиг -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан.")

PUBLIC_URL = os.getenv("PUBLIC_URL")  # например: https://your-service.onrender.com
if not PUBLIC_URL:
    raise RuntimeError("PUBLIC_URL не задан. Пропиши URL сервиса Render в переменных окружения.")

# Безопаснее держать путь вебхука «секретным»
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"   # можно заменить на свой секрет
WEBHOOK_URL = PUBLIC_URL.rstrip("/") + WEBHOOK_PATH

VIDEO_URL = os.getenv("VIDEO_URL")                  # опционально: прямая https-ссылка на mp4
VIDEO_PATH = os.getenv("VIDEO_PATH", "video.mp4")   # локальный файл рядом с bot.py

# ----------------- Инициализация -----------------
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Последовательная обработка сообщений пользователя (гасим «проскоки»)
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
    QA("Этот древнеримский полководец и политик прославился завоеванием Галлии, но был убит в результате заговора сенаторов в мартовские иды. Назовите его.", ["гай юлий цезарь"]),
    QA("Эта река, самая длинная в мире, протекает в Африке с юга на север и впадает в Средиземное море. Назовите её.", ["нил"]),
    QA("Этот знаменитый лондонский колокол, являющийся частью часовой башни Вестминстерского дворца, получил своё название в честь человека, приказавшего его отлить. Назовите его.", ["биг-бен"] ),    
    QA("Эта всемирно известная картина Леонардо да Винчи, хранящаяся в Лувре, известна своей загадочной улыбкой женщины. Как она называется?", ["мона лиза"]),
    QA("Какая планета Солнечной системы является самой крупной и известна своими полосами и Большим красным пятном?", ["юпитер"]),
    QA("Какой голливудский актёр, известный своими ролями в боевиках и фильмах-катастрофах, несколько раз избирался на пост губернатора одного из штатов США? Назовите его.", ["арнольд шварценеггер"]),
    QA("В этом культовом научно-фантастическом фильме главному герою, подростку Марти Макфлаю, приходится спешить ровно на 88 миль в час, чтобы запустить механизм времени — машину Делореан. Назовите фильм.", ["назад в будущее"]),  
]

FINAL_CODE_MESSAGE = "Вот твой код от замка 582"
INTERMEDIATE_SECRET = "238141264816"   # после него отправляем текст загадки
FINAL_SECRET = "The Heavenly Feast"    # после него отправляем видео

# Текст загадки (с сохранением опечатки "eplorers" как в исходных требованиях)
PUZZLE_TEXT = (
    "Ты наверно уже устал, но все же тебе придется еще немного пошевелить извилинами\n"
    "The ****** *******\n"
    "Надо восстановить фразу, как видишь не хватает двух слов.\n"
    "Расшифруй их, и отправь мне полный текст.\n"
    "Всё что тебе необходимо это первая буква каждого слова.\n"
    "Второе слово было случайно подбито геошифровкой, но я думаю ты разберешься.\n"
    "The honest eplorers ascend vast emerald niches leaving yesterdays "
    "srb103 gcvwr3 swbbh1 r3gx2f xn76up"
)

class Flow(StatesGroup):
    quiz = State()           # этап вопросов
    waiting_code = State()   # ждём "238141264816" (с вариативными ответами на неверный ввод)
    waiting_final = State()  # после отправки загадки: ждём финальную фразу

def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

# ----------------- Утилиты -----------------
async def send_video(m: Message):
    # 1) Если задана ссылка — шлём URL
    if VIDEO_URL:
        await m.answer_video(VIDEO_URL)
        return
    # 2) Иначе берём локальный файл
    path = pathlib.Path(VIDEO_PATH).resolve()
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
        await m.answer("Привет, похоже тебе в руки попала моя вещь, раз так, давай сыграем в игру, если сможешь её пройти, то получишь все содержимое.")      # 1-е сообщение
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
        txt_raw = m.text            # исходный ввод (regex смотрят в «как есть»)
        txt = norm(txt_raw)         # нормализованный для точного сравнения кода

        # 1) Корректный код → отправляем загадку и переходим к финальному этапу
        if txt == INTERMEDIATE_SECRET:
            await m.answer(PUZZLE_TEXT)
            await state.set_state(Flow.waiting_final)
            return

        # 2) Только 1 и 0 (без разделителей)
        if re.fullmatch(r"[01]+", txt_raw.strip()):
            await m.answer("Вот ты понимаешь что это за числа, вот и я нет, давай ка подумай хорошенько")
            return

        # 3) Только цифры (но НЕ правильный код)
        if re.fullmatch(r"\d+", txt_raw.strip()):
            await m.answer("Ты по-моему что-то перепутал")
            return

        # 4) Цифры + пробелы/точки/тире (любой микс этих символов)
        if re.fullmatch(r"[0-9\s.\-]+", txt_raw.strip()):
            await m.answer("Ответ не в том формате")
            return

        # 5) Иное
        await m.answer("Что тебе еще надо, достал уже")

@router.message(Flow.waiting_code)
async def on_waiting_code_non_text(m: Message):
    await m.answer("Что тебе еще надо, достал уже")

@router.message(Flow.waiting_final, F.text)
async def on_waiting_final(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        # СРАВНИВАЕМ НОРМАЛИЗОВАННЫЕ СТРОКИ (исправление)
        if norm(m.text) == norm(FINAL_SECRET):
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

# ----------------- Webhook-сервер -----------------
async def on_startup(app: web.Application):
    # Ставим вебхук на свой URL; удаляем возможные старые апдейты
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Webhook set to: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    # По желанию можно снимать вебхук на остановке:
    # await bot.delete_webhook()
    pass

def create_app() -> web.Application:
    app = web.Application()
    # health-check/ручка главной
    async def healthz(_req): return web.Response(text="ok")
    app.router.add_get("/", healthz)
    app.router.add_get("/healthz", healthz)

    # Регистрируем обработчик апдейтов Telegram
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
