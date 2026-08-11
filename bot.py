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
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from storage import (
    get_global_settings, 
    update_global_settings, 
    register_user, 
    unregister_user,
    get_registered_users,
    get_token_stats_text,
    init_default_prompts_if_empty
)
from gemini_client import GeminiWrapper, split_message
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
    waiting_for_master_prompt = State()
    waiting_for_tz = State()

@dp.message.outer_middleware()
async def auto_register_middleware(handler, event: Message, data):
    if event.from_user:
        register_user(event.from_user.id)
    return await handler(event, data)


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
        [KeyboardButton(text="📌 Нормативы"), KeyboardButton(text="📌 Юань")],
        [KeyboardButton(text="◀️ В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_prompt_settings_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="📝 Промпт «Нормативы»")],
        [KeyboardButton(text="📅 День (Нормативы)"), KeyboardButton(text="⏰ Время (Нормативы)")],
        [KeyboardButton(text="📝 Промпт «Юань»")],
        [KeyboardButton(text="📅 День (Юань)"), KeyboardButton(text="⏰ Время (Юань)")],
        [KeyboardButton(text="◀️ В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_general_settings_keyboard(is_paused: bool) -> ReplyKeyboardMarkup:
    pause_btn_text = "▶️ Возобновить" if is_paused else "⏸ Пауза"

    kb = [
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="🪙 Расход токенов")],
        [KeyboardButton(text="🧙‍♂️ Мастер-промпт"), KeyboardButton(text="🌍 Часовой пояс")],
        [KeyboardButton(text=pause_btn_text), KeyboardButton(text="❌ Отписаться")],
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
            InlineKeyboardButton(text="🧙‍♂️ Мастер-промпт", callback_data="show_full_mp"),
            InlineKeyboardButton(text="📌 Нормативный", callback_data="show_full_p1"),
            InlineKeyboardButton(text="📌 Юаневый", callback_data="show_full_p2")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ХЕЛПЕРЫ ---

async def render_master_prompt_menu(event: Message | CallbackQuery):
    settings = get_global_settings()
    current_mp = settings.get("master_prompt", "").strip()
    
    if not current_mp:
        status_mp = "❌ <i>Не задан (пустой)</i>"
    else:
        status_mp = f"<pre><code>{html.escape(current_mp[:3800])}</code></pre>"

    text = (
        f"🧙‍♂️ <b>Управление Мастер-промптом (System Instruction):</b>\n\n"
        f"<b>Текущий Мастер-промпт</b> (нажмите на текст, чтобы скопировать):\n{status_mp}\n\n"
        f"ℹ️ Мастер-промпт задает роль и правила для Gemini AI, которые применяются ко всем входящим запросам."
    )

    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=get_master_prompt_inline_keyboard(), parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=get_master_prompt_inline_keyboard(), parse_mode="HTML")


# --- ОБРАБОТЧИКИ ---

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
    settings = get_global_settings()
    await call.message.answer("🛠 Раздел: Общие настройки", reply_markup=get_general_settings_keyboard(settings.get("is_paused", False)))

@dp.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_process(call: CallbackQuery, state: FSMContext):
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
    ]:
        await call.message.answer("❌ Изменение отменено.", reply_markup=get_prompt_settings_keyboard())

    elif curr_state == SettingsFSM.waiting_for_master_prompt.state:
        await call.message.answer("❌ Изменение отменено.")
        await render_master_prompt_menu(call)

    elif curr_state == SettingsFSM.waiting_for_tz.state:
        settings = get_global_settings()
        await call.message.answer("❌ Изменение отменено.", reply_markup=get_general_settings_keyboard(settings.get("is_paused", False)))

    else:
        await call.message.answer("❌ Изменение отменено.", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text == "🚀 Отправить сейчас")
async def send_now_choose(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚀 Какой промпт выполнить прямо сейчас?",
        reply_markup=get_send_now_keyboard()
    )

@dp.message(F.text == "📌 Нормативы")
async def send_now_normativy(message: Message):
    settings = get_global_settings()
    prompt = settings.get("prompt_1")
    user_id = message.from_user.id
    await message.answer("⏳ Запрашиваю ответ по промпту «Нормативы»... (напишите /stop для отмены)")
    
    task = asyncio.create_task(gemini.generate(prompt))
    active_generations[user_id] = task
    try:
        res = await task
        for chunk in split_message(res):
            await message.answer(chunk)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при выполнении запроса: {e}")
    finally:
        active_generations.pop(user_id, None)

@dp.message(F.text == "📌 Юань")
async def send_now_yuan(message: Message):
    settings = get_global_settings()
    prompt = settings.get("prompt_2")
    user_id = message.from_user.id
    await message.answer("⏳ Запрашиваю ответ по промпту «Юань»... (напишите /stop для отмены)")
    
    task = asyncio.create_task(gemini.generate(prompt))
    active_generations[user_id] = task
    try:
        res = await task
        for chunk in split_message(res):
            await message.answer(chunk)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при выполнении запроса: {e}")
    finally:
        active_generations.pop(user_id, None)

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
        for chunk in split_message(response_text):
            await message.answer(chunk)
    except asyncio.CancelledError:
        logging.info(f"Генерация отменена пользователем {user_id}")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при запросе к Gemini: {e}")
    finally:
        active_generations.pop(user_id, None)

@dp.message(F.text == "⚙️ Настройка отправки")
async def menu_prompt_settings(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⚙️ Раздел: Настройка расписания рассылок", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text.in_(["📝 Промпт «Нормативы»", "📝 Нормативы"]))
async def prompt_1_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_daily_prompt_1)
    await message.answer("Пришлите новый текст промпта «Нормативы»:", reply_markup=get_cancel_inline_keyboard())

@dp.message(SettingsFSM.waiting_for_daily_prompt_1)
async def prompt_1_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_1": message.text.strip()})
    scheduler.schedule_global_tasks()
    await state.clear()
    await message.answer("✅ Промпт «Нормативы» успешно обновлён!", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text.in_(["📅 День (Нормативы)", "📅 День №1"]))
async def day_1_start(message: Message):
    await message.answer("Выберите день недели для промпта «Нормативы»:", reply_markup=get_days_inline_keyboard("set_p1_day_"))

@dp.callback_query(F.data.startswith("set_p1_day_"))
async def day_1_chosen(call: CallbackQuery):
    day_idx = int(call.data.split("_")[3])
    update_global_settings({"day_1": day_idx})
    scheduler.schedule_global_tasks()
    await call.message.edit_text(f"✅ День отправки промпта «Нормативы» изменён на {DAY_NAMES[day_idx]}")
    await call.answer()

@dp.message(F.text.in_(["⏰ Время (Нормативы)", "⏰ Время №1"]))
async def time_1_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_daily_time_1)
    await message.answer("Введите время отправки «Нормативов» в формате ЧЧ:ММ (например, 13:30):", reply_markup=get_cancel_inline_keyboard())

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
    await state.clear()
    await message.answer(f"✅ Время отправки промпта «Нормативы» изменено на {formatted_time}", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text.in_(["📝 Промпт «Юань»", "📝 Юань"]))
async def prompt_2_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_daily_prompt_2)
    await message.answer("Пришлите новый текст промпта «Юань»:", reply_markup=get_cancel_inline_keyboard())

@dp.message(SettingsFSM.waiting_for_daily_prompt_2)
async def prompt_2_finish(message: Message, state: FSMContext):
    update_global_settings({"prompt_2": message.text.strip()})
    scheduler.schedule_global_tasks()
    await state.clear()
    await message.answer("✅ Промпт «Юань» успешно обновлён!", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text.in_(["📅 День (Юань)", "📅 День №2"]))
async def day_2_start(message: Message):
    await message.answer("Выберите день недели для промпта «Юань»:", reply_markup=get_days_inline_keyboard("set_p2_day_"))

@dp.callback_query(F.data.startswith("set_p2_day_"))
async def day_2_chosen(call: CallbackQuery):
    day_idx = int(call.data.split("_")[3])
    update_global_settings({"day_2": day_idx})
    scheduler.schedule_global_tasks()
    await call.message.edit_text(f"✅ День отправки промпта «Юань» изменён на {DAY_NAMES[day_idx]}")
    await call.answer()

@dp.message(F.text.in_(["⏰ Время (Юань)", "⏰ Время №2"]))
async def time_2_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_daily_time_2)
    await message.answer("Введите время отправки «Юаня» в формате ЧЧ:ММ (например, 14:00):", reply_markup=get_cancel_inline_keyboard())

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
    await state.clear()
    await message.answer(f"✅ Время отправки промпта «Юань» изменено на {formatted_time}", reply_markup=get_prompt_settings_keyboard())

@dp.message(F.text == "🛠 Общие настройки")
async def menu_general_settings(message: Message, state: FSMContext):
    await state.clear()
    settings = get_global_settings()
    kb = get_general_settings_keyboard(settings.get("is_paused", False))
    await message.answer("🛠 Раздел: Общие настройки", reply_markup=kb)

@dp.message(F.text == "❌ Отписаться")
async def unsubscribe_user_handler(message: Message):
    unregister_user(message.from_user.id)
    settings = get_global_settings()
    kb = get_general_settings_keyboard(settings.get("is_paused", False))
    await message.answer("❌ Вы успешно отписались от рассылки.", reply_markup=kb)

@dp.message(F.text == "🧙‍♂️ Мастер-промпт")
async def menu_master_prompt(message: Message, state: FSMContext):
    await state.clear()
    await render_master_prompt_menu(message)

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

@dp.message(F.text == "📊 Статус")
async def show_status(message: Message):
    try:
        settings = get_global_settings()
        users = get_registered_users()
        
        status_str = "⏸ На паузе" if settings.get("is_paused") else "▶️ Активна"
            
        day1_idx = settings.get("day_1", 4) % 7
        day2_idx = settings.get("day_2", 1) % 7
        
        p1 = settings.get('prompt_1', '')
        p1_clean = p1.replace('\n', ' ')
        p1_preview = html.escape((p1_clean[:100] + "...") if len(p1_clean) > 100 else p1_clean)
        
        p2 = settings.get('prompt_2', '')
        p2_clean = p2.replace('\n', ' ')
        p2_preview = html.escape((p2_clean[:100] + "...") if len(p2_clean) > 100 else p2_clean)
        
        mp = settings.get("master_prompt", "").strip()
        if not mp:
            mp_preview = "Не задан (пустой)"
        else:
            mp_clean = mp.replace('\n', ' ')
            mp_preview = html.escape((mp_clean[:100] + "...") if len(mp_clean) > 100 else mp_clean)

        text = (
            f"⚙️ <b>Текущие настройки системы:</b>\n\n"
            f"📌 Состояние: {status_str}\n"
            f"👥 Всего подписчиков: {len(users)}\n"
            f"🌍 Часовой пояс: {html.escape(str(settings.get('timezone')))}\n\n"
            f"📋 <b>Нормативная рассылка:</b>\n"
            f"⏰ День и время: {DAY_NAMES[day1_idx]} в {settings.get('time_1')}\n"
            f"💬 Промпт: {p1_preview}\n\n"
            f"📋 <b>Юаневая рассылка:</b>\n"
            f"⏰ День и время: {DAY_NAMES[day2_idx]} в {settings.get('time_2')}\n"
            f"💬 Промпт: {p2_preview}\n\n"
            f"🧙‍♂️ <b>Мастер-промпт:</b> {mp_preview}\n\n"
            f"🔍 Посмотреть полный промпт:"
        )
        
        await message.answer(text, reply_markup=get_status_inline_keyboard(), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка в show_status: {e}")
        await message.answer(f"⚠️ Ошибка при формировании статуса: {e}")

@dp.callback_query(F.data.startswith("show_full_"))
async def show_full_prompt_process(call: CallbackQuery):
    await call.answer()
    target = call.data.replace("show_full_", "")
    settings = get_global_settings()

    if target == "mp":
        title = "🧙‍♂️ Полный текст Мастер-промпта:"
        val = settings.get("master_prompt", "").strip() or "Мастер-промпт не задан (пустой)."
    elif target == "p1":
        title = "📌 Полный текст Нормативного промпта:"
        val = settings.get("prompt_1", "").strip() or "Промпт не задан."
    elif target == "p2":
        title = "📌 Полный текст Юаневого промпта:"
        val = settings.get("prompt_2", "").strip() or "Промпт не задан."
    else:
        return

    msg = f"{title}\n\n{val}"
    for chunk in split_message(msg):
        await call.message.answer(chunk)

@dp.message(F.text == "🪙 Расход токенов")
async def show_token_stats(message: Message):
    stats_text = get_token_stats_text()
    await message.answer(stats_text)

@dp.message(F.text == "🌍 Часовой пояс")
async def tz_change_start(message: Message, state: FSMContext):
    await state.set_state(SettingsFSM.waiting_for_tz)
    await message.answer("Введите часовой пояс (например, Europe/Moscow, Asia/Tashkent, UTC):", reply_markup=get_cancel_inline_keyboard())

@dp.message(SettingsFSM.waiting_for_tz)
async def tz_change_finish(message: Message, state: FSMContext):
    tz_str = message.text.strip()
    try:
        ZoneInfo(tz_str)
    except Exception:
        await message.answer("❌ Некорректный часовой пояс.", reply_markup=get_cancel_inline_keyboard())
        return
    update_global_settings({"timezone": tz_str})
    scheduler.schedule_global_tasks()
    await state.clear()
    settings = get_global_settings()
    await message.answer(f"✅ Часовой пояс изменён на {tz_str}", reply_markup=get_general_settings_keyboard(settings["is_paused"]))

@dp.message(F.text.in_(["⏸ Пауза", "▶️ Возобновить"]))
async def toggle_pause(message: Message):
    settings = get_global_settings()
    new_status = not settings.get("is_paused", False)
    update_global_settings({"is_paused": new_status})
    scheduler.schedule_global_tasks()
    kb = get_general_settings_keyboard(new_status)
    msg = "⏸ Рассылка поставлена на паузу для всех." if new_status else "▶️ Рассылка успешно возобновлена для всех!"
    await message.answer(msg, reply_markup=kb)

async def main():
    # Заполняем промпты только если база пустая (пользовательские данные теперь НЕ сбрасываются)
    init_default_prompts_if_empty()
    
    scheduler.start()
    scheduler.schedule_global_tasks()
    users = get_registered_users()
    logging.info(f"Бот запущен. Активных подписчиков: {len(users)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
