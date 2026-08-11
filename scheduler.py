import asyncio
import logging
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot

from storage import get_global_settings, get_registered_users, mark_prompt_completed_today
from gemini_client import GeminiWrapper, split_message

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

class BotScheduler:
    def __init__(self, bot: Bot, gemini: GeminiWrapper):
        self.bot = bot
        self.gemini = gemini
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.start()
        logging.info("Планировщик задач запущен.")

    def schedule_global_tasks(self):
        settings = get_global_settings()

        for jid in ["global_daily_job_1", "global_daily_job_2"]:
            if self.scheduler.get_job(jid):
                self.scheduler.remove_job(jid)

        if settings.get("is_paused", False):
            logging.info("Глобальная рассылка находится на паузе.")
            return

        tz_str = settings.get("timezone", "Europe/Moscow")
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("UTC")

        # Задача №1 (Нормативы)
        d1 = settings.get("day_1", 4)
        t1_str = settings.get("time_1", "13:30")
        h1, m1 = map(int, t1_str.split(":"))
        self.scheduler.add_job(
            self.run_scheduled_prompt,
            trigger=CronTrigger(day_of_week=d1, hour=h1, minute=m1, timezone=tz),
            id="global_daily_job_1",
            args=[1],
            replace_existing=True
        )

        # Задача №2 (Юань)
        d2 = settings.get("day_2", 1)
        t2_str = settings.get("time_2", "14:00")
        h2, m2 = map(int, t2_str.split(":"))
        self.scheduler.add_job(
            self.run_scheduled_prompt,
            trigger=CronTrigger(day_of_week=d2, hour=h2, minute=m2, timezone=tz),
            id="global_daily_job_2",
            args=[2],
            replace_existing=True
        )

        logging.info(
            f"Расписание обновлено: "
            f"«Нормативы» ({DAY_NAMES[d1]} {t1_str}), "
            f"«Юань» ({DAY_NAMES[d2]} {t2_str})"
        )

    async def run_scheduled_prompt(self, prompt_num: int = 1):
        settings = get_global_settings()
        prompt = settings.get(f"prompt_{prompt_num}", "")
        prompt_title = "«Нормативы»" if prompt_num == 1 else "«Юань»"
        users = get_registered_users()

        if not users:
            logging.info("Нет подписчиков для рассылки.")
            return

        logging.info(f"Запуск рассылки {prompt_title} для {len(users)} пользователей.")
        try:
            # Передаем is_scheduled=True, чтобы снять ограничения резерва токенов
            response_text = await self.gemini.generate(prompt, is_scheduled=True)
            
            # Отмечаем, что промпт выполнен сегодня, чтобы снять дневной резерв
            mark_prompt_completed_today(prompt_num)
            
            chunks = split_message(response_text)
            
            for user_id in users:
                try:
                    for chunk in chunks:
                        await self.bot.send_message(user_id, chunk)
                        await asyncio.sleep(0.05)
                except Exception as u_err:
                    logging.error(f"Не удалось отправить пользователю {user_id}: {u_err}")

        except Exception as e:
            logging.error(f"Ошибка при выполнении рассылки {prompt_title}: {e}")
