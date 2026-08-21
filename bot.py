import os
import re
import html
import logging
import asyncio
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from storage import (
    get_global_settings, 
    update_global_settings, 
    register_user, 
    unregister_user,
    is_user_registered,
    get_registered_users,
    get_token_stats_text,
    init_default_prompts_if_empty
)
from gemini_client import GeminiWrapper, split_message, send_rich_text
from scheduler import BotScheduler, DAY_NAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Пожалуйста, проверьте переменные окружения! TELEGRAM_BOT_TOKEN и GEMINI_API_KEY должны быть заданы в .env.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
gemini = GeminiWrapper(api_key=GEMINI_API_KEY)
scheduler = BotScheduler(bot=bot, gemini=gemini)

active_generations: dict[int, asyncio.Task] = {}

class SettingsFSM(StatesGroup):
    waiting_for_ask_gemini = State()
    waiting_for_daily_prompt_1 = State()
    waiting_for_daily_time_1 = State()
    waiting_for_daily_prompt_2 = State()
    waiting_for_daily_time_2 = State()
    waiting_for_daily_prompt_3 = State()
    waiting_for_daily_day_3 = State()
    waiting_for_daily_time_3 = State()
    waiting_for_daily_prompt_4 = State()
    waiting_for_daily_day_4 = State()
    waiting_for_daily_time_4 = State()
    waiting_for_master_prompt = State()
    waiting_for_tz = State()


# --- КЛАВИАТУРЫ ---

def get_main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🚀 Отправить сейчас")],
        [KeyboardButton(text="💬 Спросить у Gemini AI")],
        [KeyboardButton(text="⚙️ Настройка отправки")],
        [KeyboardButton(text="🛠 Общие настройки")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_send_now_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="📌 1W ликвидность"), KeyboardButton(text="📌 1W CNY")],
        [KeyboardButton(text="📌 Погашения CNY"), KeyboardButton(text="📌  1M КУАП")],
        [KeyboardButton(text="◀️ В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_prompt_select_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="📌 1. 1W ликвидность")],
        [KeyboardButton(text="📌 2. 1W CNY")],
        [KeyboardButton(text="📌 3. Погашения CNY")],
        [KeyboardButton(text="📌 4.  1M КУАП")],
        [KeyboardButton(text="🧙‍♂️ 5. Мастер-промпт")],
        [KeyboardButton(text="◀️ В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_single_prompt_settings_keyboard(prompt_num: int) -> ReplyKeyboardMarkup:
    day_btn_text = "📅 День недели" if prompt_num in [1, 2] else "📅 Число месяца"
    kb = [
        [KeyboardButton(text="📝 Изменить текст")],
        [KeyboardButton(text=day_btn_text), KeyboardButton(text="⏰ Время отправки")],
        [KeyboardButton(text="◀️ Назад к выбору промпта")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_general_settings_keyboard(is_paused: bool, is_subscribed: bool = True) -> ReplyKeyboardMarkup:
    pause_btn_text = "▶️ Возобновить" if is_paused else "⏸ Пауза"
    sub_btn_text = "❌ Отписаться" if is_subscribed else "✅ Подписаться"
    kb = [
        [KeyboardButton(text="ℹ️ Инфо"), KeyboardButton(text="🪙 Расход токенов")],
        [KeyboardButton(text="🌍 Часовой пояс"), KeyboardButton(text=pause_btn_text)],
        [KeyboardButton(text=sub_btn_text)],
        [KeyboardButton(text="◀️ В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_inline_keyboard(callback_data: str = "cancel_fsm") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]]
    )

def get_master_prompt_inline_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить Мастер-промпт", callback_data="master_prompt_edit")],
        [InlineKeyboardButton(text="❌ Очистить (Сделать пустым)", callback_data="master_prompt_clear")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_master_prompt")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_days_inline_keyboard(prefix: str = "set_day_") -> InlineKeyboardMarkup:
    buttons = []
    for idx, day_name in enumerate(DAY_NAMES):
        buttons.append([InlineKeyboardButton(text=day_name, callback_data=f"{prefix}{idx}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_status_inline_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📌 Нормативный", callback_data="show_full_p1"),
            InlineKeyboardButton(text="📌 Юаневый", callback_data="show_full_p2")
        ],
        [
            InlineKeyboardButton(text="📌 Погашения CNY", callback_data="show_full_p3"),
            InlineKeyboardButton(text="📌  1M КУАП", callback_data="show_full_p4")
        ],
        [
            InlineKeyboardButton(text="🧙‍♂️ Мастер-промпт", callback_data="show_full_mp")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ХЕЛПЕРЫ ВЫВОДА ---

async def render_single_prompt_menu(message: Message, prompt_num: int, state: FSMContext):
    await state.update_data(selected_prompt=prompt_num)
    settings = get_global_settings()
    
    titles = {1: "«1W ликвидность»", 2: "«1W CNY»", 3: "«Погашения CNY»", 4: "« 1M КУАП»"}
    p_title = titles.get(prompt_num, f"Промпт №{prompt_num}")
    
    p_text = settings.get(f"prompt_{prompt_num}", "").strip()
    p_clean = p_text.replace('\n', ' ')
    p_preview = html.escape((p_clean[:150] + "...") if len(p_clean) > 150 else p_clean)
    
    if prompt_num in [1, 2]:
        day_idx = settings.get(f"day_{prompt_num}", 0) % 7
        schedule_info = f"🗓 День недели: <b>{DAY_NAMES[day_idx]}</b>"
    else:
        day_num = settings.get(f"day_{prompt_num}", 1)
        schedule_info = f"🗓 Число месяца: <b>{day_num}-е число</b>"
        
    time_info = settings.get(f"time_{prompt_num}", "12:00")

    msg_text = (
        f"⚙️ <b>Настройка промпта {p_title}:</b>\n\n"
        f"{schedule_info}\n"
        f"⏰ Время отправки: <b>{time_info}</b>\n\n"
        f"📝 <b>Предпросмотр текста:</b>\n<i>{p_preview}</i>"
    )
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Посмотреть полностью", callback_data=f"show_full_p{prompt_num}")]
    ])
    
    await message.answer(
        "📱 Выберите действие в меню ниже:", 
        reply_markup=get_single_prompt_settings_keyboard(prompt_num)
    )
    
    await message.answer(
        msg_text, 
        reply_markup=inline_kb,
        parse_mode="HTML"
    )

async def render_master_prompt_menu(event: Message | CallbackQuery):
    settings = get_global_settings()
    current_mp = settings.get("master_prompt", "").strip()
    
    if not current_mp:
        status_mp = "❌ <i>Не задан (пустой)</i>"
    else:
        status_mp = f"<pre><code>{html.escape(current_mp[:3800])}</code></pre>"

    text = (
        f"🧙‍♂️ <b>Управление Мастер-промптом (System Instruction):</b>\n\n"
        f"<b>Текущий Мастер-промпт:</b>\n{status_mp}\n\n"
        f"ℹ️ Мастер-промпт задает роль и правила для Gemini AI, которые применяются ко всем входящим запросам."
    )

    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text, reply_markup=get_master_prompt_inline_keyboard(), parse_mode="HTML")


# --- ОБРАБОТЧИКИ ---

RESERVED_MENU_TEXTS = {
    "🚀 Отправить сейчас", "💬 Спросить у Gemini AI", "⚙️ Настройка отправки", "🛠 Общие настройки",
    "📌 1W ликвидность", "📌 1W CNY", "📌 Погашения CNY", "📌  1M КУАП", "◀️ В главное меню",
    "📌 1. 1W ликвидность", "📌 2. 1W CNY", "📌 3. Погашения CNY", "📌 4.  1M КУАП", "🧙‍♂️ 5. Мастер-промпт",
    "📝 Изменить текст", "⏰ Время отправки", "◀️ Назад к выбору промпта",
    "📅 День недели", "📅 Число месяца",
    "ℹ️ Инфо", "🪙 Расход токенов", "🌍 Часовой пояс",
    "⏸ Пауза", "▶️ Возобновить", "❌ Отписаться", "✅ Подписаться",
}

FREE_TEXT_STATES = [
    SettingsFSM.waiting_for_ask_gemini,
    SettingsFSM.waiting_for_daily_prompt_1,
    SettingsFSM.waiting_for_daily_prompt_2,
    SettingsFSM.waiting_for_daily_prompt_3,
    SettingsFSM.waiting_for_daily_day_3,
    SettingsFSM.waiting_for_daily_time_1,
    SettingsFSM.waiting_for_daily_time_2,
    SettingsFSM.waiting_for_daily_time_3,
    SettingsFSM.waiting_for_daily_prompt_4,
    SettingsFSM.waiting_for_daily_day_4,
    SettingsFSM.waiting_for_daily_time_4,
    SettingsFSM.waiting_for_master_prompt,
    SettingsFSM.waiting_for_tz,
]

@dp.message(StateFilter(*FREE_TEXT_STATES), F.text.in_(RESERVED_MENU_TEXTS))
async def reserved_text_guard(message: Message, state: FSMContext):

    await state.clear()
    raise SkipHandler

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user.id)
    scheduler.schedule_global_tasks()
    
    await message.answer(
        "👋 Привет! Вы подключены к рассылке от Gemini AI.\n"
        "Используйте меню ниже для управления настройками.",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command(commands=["stop"]))
async def cmd_stop(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in active_generations:
        task = active_generations.get(user_id)
        if task and not task.done():
            task.cancel()
        await message.answer("🛑 Генерация остановлена!", reply_markup=get_main_keyboard())
        return

    await state.clear()
    update_global_settings({"is_paused": True})
    scheduler.schedule_global_tasks()
    
    await message.answer(
        "🛑 Единая рассылка приостановлена!",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "◀️ В главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📱 Вы перешли в Главное меню:", reply_markup=get_main_keyboard())

@dp.callback_query(F.data == "cancel_master_prompt")
async def cancel_master_prompt_process(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отмена")
    try:
        await call.message.delete()
    except Exception:
        pass

@dp.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_process(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_p = data.get("selected_prompt", 1)
    curr_state = await state.get_state()
    
    await state.clear()
    await call.answer("Действие отменено")
    
    try:
        await call.message.delete()
    except Exception:
        pass

    if curr_state == SettingsFSM.waiting_for_ask_gemini.state:
        await call.message.answer("📱 Вы перешли в Главное меню:", reply_markup=get_main_keyboard())
    elif curr_state in [
        SettingsFSM.waiting_for_daily_prompt_1.state,
        SettingsFSM.waiting_for_daily_time_1.state,
        SettingsFSM.waiting_for_daily_prompt_2.state,
        SettingsFSM.waiting_for_daily_time_2.state,
        SettingsFSM.waiting_for_daily_prompt_3.state,
        SettingsFSM.waiting_for_daily_day_3.state,
        SettingsFSM.waiting_for_daily_time_3.state,
        SettingsFSM.waiting_for_daily_prompt_4.state,
        SettingsFSM.waiting_for_daily_day_4.state,
        SettingsFSM.waiting_for_daily_time_4.state,
    ]:
        await call.message.answer("❌ Изменение отменено.")
        await render_single_prompt_menu(call.message, selected_p, state)
    elif curr_state == SettingsFSM.waiting_for_master_prompt.state:
        await call.message.answer("❌ Изменение отменено.")
        await render_master_prompt_menu(call)
    elif curr_state == SettingsFSM.waiting_for_tz.state:
        settings = get_global_settings()
        is_sub = is_user_registered(call.from_user.id)
        await call.message.answer("❌ Изменение отменено.", reply_markup=get_general_settings_keyboard(settings.get("is_paused", False), is_subscribed=is_sub))
    else:
        await call.message.answer("📱 Вы перешли в Главное меню:", reply_markup=get_main_keyboard())

@dp.message(F.text == "🚀 Отправить сейчас")
async def send_now_choose(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚀 Какой промпт выполнить прямо сейчас?",
        reply_markup=get_send_now_keyboard()
    )

async def _run_prompt_now(message: Message, prompt_key: str, title: str):
    settings = get_global_settings()
    prompt = settings.get(prompt_key)
    user_id = message.from_user.id
    await message.answer(f"⏳ Запрашиваю ответ по промпту «{title}»... (напишите /stop для отмены)")
    
    task = asyncio.create_task(gemini.generate(prompt))
    active_generations[user_id] = task
    try:
        res = await task
        await send_rich_text(bot, message.chat.id, res)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при выполнении запроса: {e}")
    finally:
        active_generations.pop(user_id, None)

@dp.message(F.text == "📌 1W ликвидность")
async def send_now_normativy(message: Message):
    await _run_prompt_now(message, "prompt_1", "1W ликвидность")

@dp.message(F.text == "📌 1W CNY")
async def send_now_yuan(message: Message):
    await _run_prompt_now(message, "prompt_2", "1W CNY")

@dp.message(F.text == "📌 Погашения CNY")
async def send_now_bonds(message: Message):
    await _run_prompt_now(message, "prompt_3", "Погашения CNY")

@dp.message(F.text == "📌  1M КУАП")
async def send_now_kuap(message: Message):
    await _run_prompt_now(message, "prompt_4", " 1M КУАП")

@dp.message(F.text == "💬 Спросить у Gemini AI")
async def ask_gemini_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_ask_gemini)
    await message.answer(
        "💬 Напишите ваш вопрос для Gemini AI:", 
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(SettingsFSM.waiting_for_ask_gemini)
async def ask_gemini_finish(message: Message, state: FSMContext):
    user_question = message.text.strip()
    user_id = message.from_user.id
    await state.clear()
    
    await message.answer("⏳ Думаю над ответом... (напишите /stop для отмены)")
    
    task = asyncio.create_task(gemini.generate(user_question))
    active_generations[user_id] = task

    try:
        response_text = await task
        await send_rich_text(bot, message.chat.id, response_text)
    except asyncio.CancelledError:
        logging.info(f"Генерация отменена пользователем {user_id}")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при запросе к Gemini: {e}")
    finally:
        active_generations.pop(user_id, None)


# --- НАСТРОЙКА ОТПРАВКИ (ИЕРАРХИЧЕСКОЕ МЕНЮ) ---

@dp.message(F.text.in_(["⚙️ Настройка отправки", "◀️ Назад к выбору промпта"]))
async def menu_prompt_settings(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚙️ <b>Выберите промпт для настройки расписания и текста:</b>", 
        reply_markup=get_prompt_select_keyboard(),
        parse_mode="HTML"
    )

@dp.message(F.text == "📌 1. 1W ликвидность")
async def choose_prompt_1(message: Message, state: FSMContext):
    await render_single_prompt_menu(message, 1, state)

@dp.message(F.text == "📌 2. 1W CNY")
async def choose_prompt_2(message: Message, state: FSMContext):
    await render_single_prompt_menu(message, 2, state)

@dp.message(F.text == "📌 3. Погашения CNY")
async def choose_prompt_3(message: Message, state: FSMContext):
    await render_single_prompt_menu(message, 3, state)

@dp.message(F.text == "📌 4.  1M КУАП")
async def choose_prompt_4(message: Message, state: FSMContext):
    await render_single_prompt_menu(message, 4, state)

@dp.message(F.text == "🧙‍♂️ 5. Мастер-промпт")
async def choose_master_prompt(message: Message, state: FSMContext):
    await state.clear()
    await render_master_prompt_menu(message)


# --- ПОДМЕНЮ: ИЗМЕНЕНИЕ ТЕКСТА / ДНЯ / ВРЕМЕНИ ---

@dp.message(F.text == "📝 Изменить текст")
async def prompt_text_edit_start(message: Message, state: FSMContext):
    data = await state.get_data()
    p_num = data.get("selected_prompt", 1)
    
    states_map = {
        1: SettingsFSM.waiting_for_daily_prompt_1,
        2: SettingsFSM.waiting_for_daily_prompt_2,
        3: SettingsFSM.waiting_for_daily_prompt_3,
        4: SettingsFSM.waiting_for_daily_prompt_4
    }
    await state.set_state(states_map[p_num])
    
    titles = {1: "«1W ликвидность»", 2: "«1W CNY»", 3: "«Погашения CNY»", 4: "« 1M КУАП»"}
    await message.answer(
        f"Пришлите новый текст промпта {titles[p_num]}:", 
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(SettingsFSM.waiting_for_daily_prompt_1)
async def prompt_1_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_1": message.text.strip()})
    scheduler.schedule_global_tasks()
    await message.answer("✅ Текст промпта «1W ликвидность» успешно обновлён!")
    await render_single_prompt_menu(message, 1, state)

@dp.message(SettingsFSM.waiting_for_daily_prompt_2)
async def prompt_2_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_2": message.text.strip()})
    scheduler.schedule_global_tasks()
    await message.answer("✅ Текст промпта «1W CNY» успешно обновлён!")
    await render_single_prompt_menu(message, 2, state)

@dp.message(SettingsFSM.waiting_for_daily_prompt_3)
async def prompt_3_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_3": message.text.strip()})
    scheduler.schedule_global_tasks()
    await message.answer("✅ Текст промпта «Погашения CNY» успешно обновлён!")
    await render_single_prompt_menu(message, 3, state)

@dp.message(SettingsFSM.waiting_for_daily_prompt_4)
async def prompt_4_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_4": message.text.strip()})
    scheduler.schedule_global_tasks()
    await message.answer("✅ Текст промпта « 1M КУАП» успешно обновлён!")
    await render_single_prompt_menu(message, 4, state)

@dp.message(F.text == "📅 День недели")
async def day_weekly_start(message: Message, state: FSMContext):
    data = await state.get_data()
    p_num = data.get("selected_prompt", 1)
    prefix = f"set_p{p_num}_day_"
    titles = {1: "«1W ликвидность»", 2: "«1W CNY»"}
    await message.answer(
        f"Выберите день недели для промпта {titles.get(p_num, '')}:", 
        reply_markup=get_days_inline_keyboard(prefix)
    )

@dp.callback_query(F.data.startswith("set_p1_day_"))
async def day_1_chosen(call: CallbackQuery, state: FSMContext):
    day_idx = int(call.data.split("_")[3])
    update_global_settings({"day_1": day_idx})
    scheduler.schedule_global_tasks()
    await call.message.answer(f"✅ День отправки изменён на {DAY_NAMES[day_idx]}")
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await render_single_prompt_menu(call.message, 1, state)

@dp.callback_query(F.data.startswith("set_p2_day_"))
async def day_2_chosen(call: CallbackQuery, state: FSMContext):
    day_idx = int(call.data.split("_")[3])
    update_global_settings({"day_2": day_idx})
    scheduler.schedule_global_tasks()
    await call.message.answer(f"✅ День отправки изменён на {DAY_NAMES[day_idx]}")
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await render_single_prompt_menu(call.message, 2, state)

@dp.message(F.text == "📅 Число месяца")
async def day_monthly_start(message: Message, state: FSMContext):
    data = await state.get_data()
    p_num = data.get("selected_prompt", 3)

    states_map = {
        3: SettingsFSM.waiting_for_daily_day_3,
        4: SettingsFSM.waiting_for_daily_day_4
    }
    await state.set_state(states_map.get(p_num, SettingsFSM.waiting_for_daily_day_3))

    titles = {3: "«Погашения CNY»", 4: "« 1M КУАП»"}
    await message.answer(
        f"Введите число месяца для отправки отчета {titles.get(p_num, '')} (число от 1 до 28):", 
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(SettingsFSM.waiting_for_daily_day_3)
async def day_3_finish(message: Message, state: FSMContext):
    val = message.text.strip()
    if not val.isdigit() or not (1 <= int(val) <= 28):
        await message.answer("❌ Введите число от 1 до 28!", reply_markup=get_cancel_inline_keyboard())
        return
    day_num = int(val)
    update_global_settings({"day_3": day_num})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ День ежемесячной отправки изменён на {day_num}-е число месяца.")
    await render_single_prompt_menu(message, 3, state)

@dp.message(SettingsFSM.waiting_for_daily_day_4)
async def day_4_finish(message: Message, state: FSMContext):
    val = message.text.strip()
    if not val.isdigit() or not (1 <= int(val) <= 28):
        await message.answer("❌ Введите число от 1 до 28!", reply_markup=get_cancel_inline_keyboard())
        return
    day_num = int(val)
    update_global_settings({"day_4": day_num})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ День ежемесячной отправки изменён на {day_num}-е число месяца.")
    await render_single_prompt_menu(message, 4, state)

@dp.message(F.text == "⏰ Время отправки")
async def time_edit_start(message: Message, state: FSMContext):
    data = await state.get_data()
    p_num = data.get("selected_prompt", 1)
    
    states_map = {
        1: SettingsFSM.waiting_for_daily_time_1,
        2: SettingsFSM.waiting_for_daily_time_2,
        3: SettingsFSM.waiting_for_daily_time_3,
        4: SettingsFSM.waiting_for_daily_time_4
    }
    await state.set_state(states_map[p_num])
    await message.answer(
        "Введите время отправки в формате ЧЧ:ММ (например, 14:30):", 
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(SettingsFSM.waiting_for_daily_time_1)
async def time_1_finish(message: Message, state: FSMContext):
    time_str = message.text.strip()
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
        await message.answer("❌ Неверный формат времени!", reply_markup=get_cancel_inline_keyboard())
        return
    h, m = time_str.split(":")
    formatted_time = f"{int(h):02d}:{int(m):02d}"
    update_global_settings({"time_1": formatted_time})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ Время отправки изменено на {formatted_time}")
    await render_single_prompt_menu(message, 1, state)

@dp.message(SettingsFSM.waiting_for_daily_time_2)
async def time_2_finish(message: Message, state: FSMContext):
    time_str = message.text.strip()
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
        await message.answer("❌ Неверный формат времени!", reply_markup=get_cancel_inline_keyboard())
        return
    h, m = time_str.split(":")
    formatted_time = f"{int(h):02d}:{int(m):02d}"
    update_global_settings({"time_2": formatted_time})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ Время отправки изменено на {formatted_time}")
    await render_single_prompt_menu(message, 2, state)

@dp.message(SettingsFSM.waiting_for_daily_time_3)
async def time_3_finish(message: Message, state: FSMContext):
    time_str = message.text.strip()
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
        await message.answer("❌ Неверный формат времени!", reply_markup=get_cancel_inline_keyboard())
        return
    h, m = time_str.split(":")
    formatted_time = f"{int(h):02d}:{int(m):02d}"
    update_global_settings({"time_3": formatted_time})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ Время отправки изменено на {formatted_time}")
    await render_single_prompt_menu(message, 3, state)

@dp.message(SettingsFSM.waiting_for_daily_time_4)
async def time_4_finish(message: Message, state: FSMContext):
    time_str = message.text.strip()
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
        await message.answer("❌ Неверный формат времени!", reply_markup=get_cancel_inline_keyboard())
        return
    h, m = time_str.split(":")
    formatted_time = f"{int(h):02d}:{int(m):02d}"
    update_global_settings({"time_4": formatted_time})
    scheduler.schedule_global_tasks()
    await message.answer(f"✅ Время отправки изменено на {formatted_time}")
    await render_single_prompt_menu(message, 4, state)


# --- ОБЩИЕ НАСТРОЙКИ И ИНФО ---

@dp.message(F.text == "🛠 Общие настройки")
async def menu_general_settings(message: Message, state: FSMContext):
    await state.clear()
    settings = get_global_settings()
    is_sub = is_user_registered(message.from_user.id)
    kb = get_general_settings_keyboard(settings.get("is_paused", False), is_subscribed=is_sub)
    await message.answer("🛠 Раздел: Общие настройки", reply_markup=kb)

@dp.message(F.text == "❌ Отписаться")
async def unsubscribe_user_handler(message: Message):
    unregister_user(message.from_user.id)
    settings = get_global_settings()
    kb = get_general_settings_keyboard(settings.get("is_paused", False), is_subscribed=False)
    await message.answer("❌ Вы успешно отписались от рассылки.", reply_markup=kb)

@dp.message(F.text == "✅ Подписаться")
async def subscribe_user_handler(message: Message):
    register_user(message.from_user.id)
    settings = get_global_settings()
    kb = get_general_settings_keyboard(settings.get("is_paused", False), is_subscribed=True)
    await message.answer("✅ Вы успешно подписались на рассылку!", reply_markup=kb)

@dp.callback_query(F.data == "master_prompt_edit")
async def master_prompt_edit_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SettingsFSM.waiting_for_master_prompt)
    await call.message.answer("Пришлите новый текст Мастер-промпта:", reply_markup=get_cancel_inline_keyboard())

@dp.callback_query(F.data == "master_prompt_clear")
async def master_prompt_clear(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    update_global_settings({"master_prompt": ""})
    await call.message.answer("✅ Мастер-промпт успешно очищен!")
    await render_master_prompt_menu(call)

@dp.message(SettingsFSM.waiting_for_master_prompt)
async def master_prompt_finish(message: Message, state: FSMContext):
    new_mp = message.text.strip()
    update_global_settings({"master_prompt": new_mp})
    await state.clear()
    await message.answer("✅ Мастер-промпт успешно обновлен!")
    await render_master_prompt_menu(message)

@dp.message(F.text.in_(["ℹ️ Инфо", "📊 Статус", "Инфо"]))
async def show_info(message: Message):
    try:
        settings = get_global_settings()
        users = get_registered_users()
        
        status_str = "⏸ На паузе" if settings.get("is_paused") else "▶️ Активна"
            
        day1_idx = settings.get("day_1", 4) % 7
        day2_idx = settings.get("day_2", 1) % 7
        day3_num = settings.get("day_3", 1)
        day4_num = settings.get("day_4", 5)
        
        def get_preview(key: str) -> str:
            text_val = settings.get(key, '').replace('\n', ' ')
            return html.escape((text_val[:80] + "...") if len(text_val) > 80 else text_val)

        p1_preview = get_preview("prompt_1")
        p2_preview = get_preview("prompt_2")
        p3_preview = get_preview("prompt_3")
        p4_preview = get_preview("prompt_4")
        
        mp = settings.get("master_prompt", "").strip()
        mp_preview = "Не задан (пустой)" if not mp else html.escape((mp.replace('\n', ' ')[:80] + "..."))

        text = (
            f"ℹ️ <b>Текущие настройки и статус системы:</b>\n\n"
            f"📌 Состояние: {status_str}\n"
            f"👥 Всего подписчиков: {len(users)}\n"
            f"🌍 Часовой пояс: {html.escape(str(settings.get('timezone')))}\n"
            f"🤖 Модель Gemini (генерация): <code>{html.escape(gemini.model_name)}</code>\n\n"
            f"📋 <b>1. 1W ликвидность (еженедельно):</b>\n"
            f"⏰ {DAY_NAMES[day1_idx]} в {settings.get('time_1')}\n"
            f"💬 {p1_preview}\n\n"
            f"📋 <b>2. 1W CNY (еженедельно):</b>\n"
            f"⏰ {DAY_NAMES[day2_idx]} в {settings.get('time_2')}\n"
            f"💬 {p2_preview}\n\n"
            f"📋 <b>3. Погашения CNY (ежемесячно):</b>\n"
            f"⏰ {day3_num}-е число месяца в {settings.get('time_3')}\n"
            f"💬 {p3_preview}\n\n"
            f"📋 <b>4.  1M КУАП (ежемесячно):</b>\n"
            f"⏰ {day4_num}-е число месяца в {settings.get('time_4')}\n"
            f"💬 {p4_preview}\n\n"
            f"🧙‍♂️ <b>Мастер-промпт:</b> {mp_preview}\n\n"
            f"🔍 Развернуть / настроить промпты:"
        )
        
        await message.answer(text, reply_markup=get_status_inline_keyboard(), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка в show_info: {e}")
        await message.answer(f"⚠️ Ошибка при формировании информации: {e}")

@dp.callback_query(F.data.startswith("show_full_"))
async def show_full_prompt_process(call: CallbackQuery):
    await call.answer()
    target = call.data.replace("show_full_", "")

    if target == "mp":
        await render_master_prompt_menu(call)
        return

    settings = get_global_settings()
    titles = {
        "p1": "📌 Полный текст промпта «1W ликвидность»",
        "p2": "📌 Полный текст промпта «1W CNY»",
        "p3": "📌 Полный текст промпта «Погашения CNY»",
        "p4": "📌 Полный текст промпта « 1M КУАП»"
    }
    keys = {"p1": "prompt_1", "p2": "prompt_2", "p3": "prompt_3", "p4": "prompt_4"}

    if target not in keys:
        return

    val = settings.get(keys[target], "").strip() or "Промпт не задан."

    # Это обычный технический просмотр текста промпта (не итоговый AI-ответ),
    # поэтому отправляем простым sendMessage с HTML-разметкой <pre><code> — без Rich Message.
    chunks = split_message(val, max_length=3500)
    total = len(chunks)

    for i, chunk in enumerate(chunks, start=1):
        heading = titles[target]
        if total > 1:
            heading += f" (часть {i}/{total})"

        formatted_chunk = f"<pre><code>{html.escape(chunk)}</code></pre>"
        msg = f"<b>{html.escape(heading)}</b>\n\n{formatted_chunk}"
        if i == total:
            msg += "\n\nℹ️ <i>Изменить текст этого промпта можно только через раздел меню «⚙️ Настройка отправки».</i>"

        try:
            await call.message.answer(msg, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"⚠️ Ошибка отправки полного текста промпта ({e!r}). Отправляю без HTML-разметки.")
            await call.message.answer(f"{heading}\n\n{chunk}")

@dp.message(F.text == "🪙 Расход токенов")
async def show_token_stats(message: Message):
    stats_text = get_token_stats_text()
    await message.answer(stats_text)

@dp.message(F.text == "🌍 Часовой пояс")
async def tz_change_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_tz)
    await message.answer(
        "Введите часовой пояс (например, <code>Europe/Moscow</code>, <code>Asia/Tashkent</code>, <code>UTC</code>):", 
        reply_markup=get_cancel_inline_keyboard(),
        parse_mode="HTML"
    )

@dp.message(SettingsFSM.waiting_for_tz)
async def tz_change_finish(message: Message, state: FSMContext):
    tz_str = message.text.strip()
    try:
        ZoneInfo(tz_str)
    except Exception:
        await message.answer(
            "❌ Некорректный часовой пояс.\nНажмите на пример для копирования: <code>Europe/Moscow</code>", 
            reply_markup=get_cancel_inline_keyboard(),
            parse_mode="HTML"
        )
        return
    update_global_settings({"timezone": tz_str})
    scheduler.schedule_global_tasks()
    await state.clear()
    settings = get_global_settings()
    is_sub = is_user_registered(message.from_user.id)
    await message.answer(
        f"✅ Часовой пояс изменён на <code>{html.escape(tz_str)}</code>", 
        reply_markup=get_general_settings_keyboard(settings["is_paused"], is_subscribed=is_sub),
        parse_mode="HTML"
    )

@dp.message(F.text.in_(["⏸ Пауза", "▶️ Возобновить"]))
async def toggle_pause(message: Message):
    settings = get_global_settings()
    new_status = not settings.get("is_paused", False)
    update_global_settings({"is_paused": new_status})
    scheduler.schedule_global_tasks()
    is_sub = is_user_registered(message.from_user.id)
    kb = get_general_settings_keyboard(new_status, is_subscribed=is_sub)
    msg = "⏸ Рассылка поставлена на паузу для всех." if new_status else "▶️ Рассылка успешно возобновлена для всех!"
    await message.answer(msg, reply_markup=kb)

async def main():
    init_default_prompts_if_empty()
    
    scheduler.start()
    scheduler.schedule_global_tasks()
    users = get_registered_users()
    logging.info(f"Бот запущен. Активных подписчиков: {len(users)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
