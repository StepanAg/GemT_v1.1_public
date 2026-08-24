import asyncio
import logging
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot

from storage import get_global_settings, get_registered_users, mark_prompt_completed_today, force_reset_daily_tokens
from gemini_client import GeminiWrapper, send_rich_text

MSK_TZ = ZoneInfo("Europe/Moscow")

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

class BotScheduler:
    def __init__(self, bot: Bot, gemini: GeminiWrapper):
        self.bot = bot
        self.gemini = gemini
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.start()
        self.schedule_token_reset_job()
        logging.info("Планировщик задач запущен.")

    def schedule_token_reset_job(self):
        """Сброс суточного лимита токенов строго в 00:00 по МСК, для всех пользователей,
        независимо от паузы рассылки и часового пояса, выбранного в настройках."""
        if self.scheduler.get_job("global_token_reset"):
            self.scheduler.remove_job("global_token_reset")

        self.scheduler.add_job(
            self.run_token_reset,
            trigger=CronTrigger(hour=0, minute=0, timezone=MSK_TZ),
            id="global_token_reset",
            replace_existing=True
        )
        logging.info("Задача сброса суточного лимита токенов запланирована на 00:00 (МСК).")

    async def run_token_reset(self):
        force_reset_daily_tokens()

    def schedule_global_tasks(self):
        settings = get_global_settings()

        for jid in ["global_daily_job_1", "global_daily_job_2", "global_daily_job_3", "global_daily_job_4"]:
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

        # Задача №1 (1W ликвидность - еженедельно)
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

        # Задача №2 (1W CNY - еженедельно)
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

        # Задача №3 (Погашения CNY - ежемесячно)
        d3 = settings.get("day_3", 1)
        t3_str = settings.get("time_3", "15:00")
        h3, m3 = map(int, t3_str.split(":"))
        self.scheduler.add_job(
            self.run_scheduled_prompt,
            trigger=CronTrigger(day=d3, hour=h3, minute=m3, timezone=tz),
            id="global_daily_job_3",
            args=[3],
            replace_existing=True
        )

        # Задача №4 ( 1M КУАП для СЗКО - ежемесячно)
        d4 = settings.get("day_4", 5)
        t4_str = settings.get("time_4", "16:00")
        h4, m4 = map(int, t4_str.split(":"))
        self.scheduler.add_job(
            self.run_scheduled_prompt,
            trigger=CronTrigger(day=d4, hour=h4, minute=m4, timezone=tz),
            id="global_daily_job_4",
            args=[4],
            replace_existing=True
        )

        logging.info(
            f"Расписание обновлено: "
            f"«1W ликвидность» ({DAY_NAMES[d1]} {t1_str}), "
            f"«1W CNY» ({DAY_NAMES[d2]} {t2_str}), "
            f"«Погашения CNY» ({d3}-е число месяца в {t3_str}), "
            f"«1M КУАП» ({d4}-е число месяца в {t4_str})"
        )

    async def run_scheduled_prompt(self, prompt_num: int = 1):
        settings = get_global_settings()
        prompt = settings.get(f"prompt_{prompt_num}", "")
        
        titles = {1: "«1W ликвидность»", 2: "«1W CNY»", 3: "«Погашения CNY»", 4: "« 1M КУАП»"}
        prompt_title = titles.get(prompt_num, f"Промпт №{prompt_num}")
        
        users = get_registered_users()

        if not users:
            logging.info("Нет подписчиков для рассылки.")
            return

        logging.info(f"Запуск рассылки {prompt_title} для {len(users)} пользователей.")
        try:
            # Для готовых авто-рассылок мастер-промпт отключен (use_master_prompt=False)
            response_text = await self.gemini.generate(prompt, is_scheduled=True, use_master_prompt=False)
            mark_prompt_completed_today(prompt_num)
            
            for user_id in users:
                try:
                    await send_rich_text(self.bot, user_id, response_text)
                    await asyncio.sleep(0.05)
                except Exception as u_err:
                    logging.error(f"Не удалось отправить пользователю {user_id}: {u_err}")

        except Exception as e:
            logging.error(f"Ошибка при выполнении рассылки {prompt_title}: {e}")
