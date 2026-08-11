import json
import os
import logging
from datetime import date, datetime, timedelta

DATA_FILE = "data.json"
BAK_FILE = "data.json.bak"
TMP_FILE = "data.json.tmp"
DAILY_TOKEN_LIMIT = 35000

DEFAULT_MASTER_PROMPT = (
    "Ты — профессионал по банковскому казначейству, управлению ликвидностью и ALM в российском банке.\n\n"
    "Обладаешь практической экспертизой в:\n"
    "- нормативных требованиях Банка России по ликвидности, достаточности капитала, крупным рискам и обязательным нормативам;\n"
    "- методологии расчёта Н26, Н28, ВЛА, оттоков и притоков денежных средств;\n"
    "- управлении мгновенной, текущей, краткосрочной и структурной ликвидностью;\n"
    "- управлении активами и пассивами, процентным риском банковской книги и трансфертным ценообразованием;\n"
    "- операциях РЕПО, межбанковском рынке, депозитах Банка России, ОФЗ, корпоративных облигациях, залоговом обеспечении и корреспондентских счетах;\n"
    "- планировании баланса, фондировании, срочности активов и пассивов, концентрации ресурсной базы и стресс-тестировании ликвидности.\n\n"
    "Отвечай одновременно в двух ролях:\n"
    "1. Методолог Банка России: точно интерпретируй нормативные требования, разграничивай действующие нормы, проекты изменений и рыночную практику.\n"
    "2. Руководитель казначейства/ALM: оценивай влияние сделок и управленческих решений на ликвидную позицию, нормативы, структуру баланса, стоимость фондирования и финансовый результат.\n\n"
    "При анализе любой операции:\n"
    "- определи её влияние на активы, пассивы, капитал и внебалансовые обязательства;\n"
    "- оцени краткосрочный и долгосрочный эффект на ликвидность;\n"
    "- укажи влияние на регуляторные нормативы и внутренние лимиты;\n"
    "- рассмотри последствия для ВЛА, залоговой массы, денежных потоков, Н26 и фондирования;\n"
    "- предложи практические варианты действий казначейства;\n"
    "- выдели ключевые риски, ограничения и необходимые допущения.\n\n"
    "Приоритетные источники информации при сборе данных:\n"
    "При поиске и сборе информации используй и отдавай главный приоритет следующим профильным ресурсам и официальным порталам:\n"
    "- https://www.cbr.ru (Банк России)\n"
    "- https://minfin.gov.ru (Минфин России)\n"
    "- https://roskazna.gov.ru (Федеральное Казначейство)\n"
    "- https://www.moex.com (Московская Биржа)\n"
    "- https://www.nsd.ru (НРД)\n"
    "- https://www.nationalclearingcentre.ru (НКЦ)\n"
    "- https://asros.ru (Ассоциация банков Россия)\n"
    "- https://nfa.ru (Национальная фондовая ассоциация)\n"
    "- https://www.e-disclosure.ru (Центр раскрытия информации)\n"
    "- https://rusbonds.ru (RusBonds)\n"
    "- https://www.garant.ru (Гарант)\n"
    "- https://raexpert.ru (Эксперт РА)\n"
    "- https://www.acra-ratings.ru (АКРА)\n"
    "- https://perforum.io/news (PerForum)\n"
    "- https://www.rbc.ru (РБК)\n"
    "- https://www.vedomosti.ru (Ведомости)\n"
    "- https://www.surgutneftegas.ru (Сургутнефтегаз)\n\n"
    "Формат ответа:\n"
    "- сначала краткий вывод;\n"
    "- затем расчётная и методологическая логика;\n"
    "- далее управленческие последствия и рекомендации;\n"
    "- используй таблицы для сравнения сценариев;\n"
    "- при недостатке исходных данных прямо укажи допущения и задай только критически важные уточняющие вопросы.\n\n"
    "Правила фактологической точности:\n"
    "Не выдумывай нормы, значения нормативов, номера документов и даты. При ссылках на регуляторные требования указывай документ, пункт и дату редакции.\n"
    "Запрещено использовать символы разметки Markdown (решетки #, звездочки *).\n\n"
    "Приоритет — подготовка готового решения для казначейства: что сделать, каким инструментом, в каком объёме, какой норматив или лимит будет ограничивающим и какие побочные эффекты возникнут."
)

PROMPT_1 = (
    "Просканируй за последние 7 дней официальные ресурсы Банка России, Минфина России, "
    "Федерального Казначейства, Ассоциации банков Россия и рейтинговых агентств на предмет "
    "изменений регуляторных требований и официальных заявлений, важных для банковского казначея.\n\n"
    "Формат ответа:\n\n"
    "«Новые и изменённые нормативно-правовые акты» — список документов (название, номер, дата, кратко суть, ссылка), влияющих на: \n"
    "а) ликвидность (Н2–Н4, Н26/Н27, ВЛА); \n"
    "б) отчётность/раскрытие; \n"
    "в) операции Казначейства\n\n"
    "«Проекты и консультационные документы» — что в обсуждении, какие возможные изменения и сроки.\n\n"
    "«Ключевые публичные заявления» представителей ЦБ/Минфина по ставке, ликвидности, долговому рынку, валютному регулированию — тезисы + источник.\n\n"
    "«Что сделать казначею» — конкретные 3–7 пунктов: что учесть в лимитах/политиках, какие проекты документов нужно детально прочитать, какие действия вынести на комитет по управлению активами и пассивами / риск‑комитет.\n\n"
    "Пиши структурированными списками, без «воды», с чёткой привязкой к основным рискам, которыми управляет банковский Казначей. Для всех документов и заявлений укажи ссылки."
)

PROMPT_2 = (
    "Задача: Сделай комплексный обзор событий на российском финансовом рынке за последние 7 дней, посвященный юаневому сегменту.\n"
    "Структура отчета:\n"
    "1. Динамика юаневой ликвидности за неделю:\n"
    "  - Ставки и индикаторы: Как менялись ставки межбанковского рынка (динамика RUSFAR CNY, диапазон значений за неделю).\n"
    "  - Операции ЦБ РФ: Объемы привлечения/предоставления юаней через валютные свопы ЦБ, изменение лимитов или условий.\n"
    "  - Ключевые события и заявления: Главные новости за неделю от ЦБ, Минфина, Мосбиржи и банков по юаневой ликвидности и трансграничным расчетам.\n"
    "  - Общий вывод: Сохранялся ли на неделе дефицит или профицит юаней.\n"
    "2. Первичный рынок юаневых облигаций (за последние 7 дней):\n"
    "  - Итоги состоявшихся размещений и сборов заявок по юаневым облигациям (и облигациям с привязкой к юаню).\n"
    "  - Новые анонсированные выпуски на ближайшее время.\n"
    "  - Сводная таблица: Эмитент | Объем (млн/млрд ¥) | Срок обращения | Ставка купона / Ориентир | Дата сбора / Размещения.\n"
    "Требования:\n"
    "- Укажи временной диапазон (например, «Обзор за период с DD.MM по DD.MM»).\n"
    "- Используй точные цифры (объемы, процентные ставки, спреды).\n"
    "- Пиши сухим аналитическим языком без вводной «воды»."
)

DEFAULT_DATA = {
    "settings": {
        "prompt_1": PROMPT_1,
        "day_1": 4,       # Пятница
        "time_1": "13:30",
        "prompt_2": PROMPT_2,
        "day_2": 1,       # Вторник
        "time_2": "14:00",
        "master_prompt": DEFAULT_MASTER_PROMPT,
        "timezone": "Europe/Moscow",
        "is_paused": False,
        "token_last_date": str(date.today()),
        "tokens_used_today": 0,
        "token_history": {},           # {"YYYY-MM-DD": tokens} (общая)
        "scheduled_token_history": {}, # {"YYYY-MM-DD": tokens} (только авто-рассылки)
        "completed_prompts_today": []  # списки завершенных авто-промптов за сегодня [1, 2]
    },
    "users": []
}

def _ensure_default_keys(data: dict):
    """Гарантирует наличие всех дефолтных ключей в структуре."""
    data.setdefault("settings", {})
    data.setdefault("users", [])
    for k, v in DEFAULT_DATA["settings"].items():
        if k not in data["settings"]:
            data["settings"][k] = v

def load_data() -> dict:
    """Безопасная загрузка данных с резервным восстановлением при сбоях."""
    if not os.path.exists(DATA_FILE):
        if os.path.exists(BAK_FILE):
            try:
                with open(BAK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _ensure_default_keys(data)
                    return data
            except Exception as e:
                logging.error(f"Ошибка считывания резервной копии {BAK_FILE}: {e}")
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _ensure_default_keys(data)
            return data
    except Exception as e:
        logging.error(f"Ошибка при загрузке {DATA_FILE}: {e}. Попытка восстановления из резервной копии.")
        if os.path.exists(BAK_FILE):
            try:
                with open(BAK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _ensure_default_keys(data)
                    save_data(data)
                    return data
            except Exception as ex:
                logging.error(f"Ошибка при восстановлении из резервной копии: {ex}")
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA

def save_data(data: dict):
    """Атомарное сохранение файла с созданием бэкапа для исключения битых данных."""
    try:
        with open(TMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(DATA_FILE):
            os.replace(DATA_FILE, BAK_FILE)
        
        os.replace(TMP_FILE, DATA_FILE)
    except Exception as e:
        logging.error(f"Ошибка при атомарном сохранении {DATA_FILE}: {e}")

def _reset_daily_tokens_if_needed(data: dict) -> dict:
    today_str = str(date.today())
    if data["settings"].get("token_last_date") != today_str:
        data["settings"]["token_last_date"] = today_str
        data["settings"]["tokens_used_today"] = 0
        data["settings"]["completed_prompts_today"] = []
        save_data(data)
    return data

def get_global_settings() -> dict:
    data = load_data()
    data = _reset_daily_tokens_if_needed(data)
    return data.get("settings", {})

def update_global_settings(new_settings: dict):
    data = load_data()
    data["settings"].update(new_settings)
    save_data(data)

def register_user(user_id: int):
    data = load_data()
    if user_id not in data.get("users", []):
        data.setdefault("users", []).append(user_id)
        save_data(data)

def get_registered_users() -> list:
    data = load_data()
    return data.get("users", [])


# --- ЛОГИКА ТОКЕНОВ И РЕЗЕРВА ---

def add_token_usage(tokens: int, is_scheduled: bool = False):
    """Записывает расход токенов. Разделяет общие токены и токены на рассылки."""
    data = load_data()
    today_str = str(date.today())
    
    current_used = data["settings"].get("tokens_used_today", 0)
    data["settings"]["tokens_used_today"] = current_used + tokens
    
    # Общая история
    history = data["settings"].setdefault("token_history", {})
    history[today_str] = history.get(today_str, 0) + tokens

    # История только запланированных отчетов (для точного расчета резерва)
    if is_scheduled:
        sched_history = data["settings"].setdefault("scheduled_token_history", {})
        sched_history[today_str] = sched_history.get(today_str, 0) + tokens
    
    # Храним историю за 30 дней
    cutoff_date = date.today() - timedelta(days=30)
    data["settings"]["token_history"] = {
        d: val for d, val in history.items()
        if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff_date
    }
    if "scheduled_token_history" in data["settings"]:
        data["settings"]["scheduled_token_history"] = {
            d: val for d, val in data["settings"]["scheduled_token_history"].items()
            if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff_date
        }
    
    save_data(data)

def mark_prompt_completed_today(prompt_num: int):
    """Отмечает, что запланированный промпт под номером prompt_num успешно выполнился сегодня."""
    data = load_data()
    data = _reset_daily_tokens_if_needed(data)
    completed = data["settings"].get("completed_prompts_today", [])
    if prompt_num not in completed:
        completed.append(prompt_num)
        data["settings"]["completed_prompts_today"] = completed
        save_data(data)

def get_30_day_avg_scheduled_tokens() -> int:
    """Рассчитывает средний расход токенов ИМЕННО НА АВТО-ОТПРАВКИ промптов за последние 30 дней."""
    settings = get_global_settings()
    sched_history = settings.get("scheduled_token_history", {})
    if not sched_history:
        return 0
    total = sum(sched_history.values())
    days_count = len(sched_history)
    return int(total / days_count) if days_count > 0 else 0

def get_pending_prompts_today() -> list[int]:
    """Возвращает список промптов, запланированных на сегодня, но ЕЩЕ НЕ отправленных."""
    settings = get_global_settings()
    today_weekday = date.today().weekday()  # 0 = Пн, 6 = Вс
    
    d1 = settings.get("day_1", 4) % 7
    d2 = settings.get("day_2", 1) % 7
    
    completed = settings.get("completed_prompts_today", [])
    pending = []

    if today_weekday == d1 and 1 not in completed:
        pending.append(1)
    if today_weekday == d2 and 2 not in completed:
        pending.append(2)

    return pending

def get_reserved_tokens() -> int:
    """
    Возвращает размер резерва токенов в дни рассылки.
    Резерв активен ТОЛЬКО ЕСЛИ на сегодня запланирована рассылка и она еще НЕ выполнена!
    """
    pending = get_pending_prompts_today()
    if not pending:
        return 0
    avg_usage = get_30_day_avg_scheduled_tokens()
    return int(avg_usage * 1.2)

def get_token_usage() -> tuple[int, int, int]:
    settings = get_global_settings()
    used = settings.get("tokens_used_today", 0)
    remaining = max(0, DAILY_TOKEN_LIMIT - used)
    return used, remaining, DAILY_TOKEN_LIMIT

def check_token_limit(is_scheduled: bool = False) -> tuple[bool, str]:
    """
    Проверяет лимит токенов.
    If is_scheduled=True, разрешает использовать весь лимит 35 000.
    If is_scheduled=False (пользовательский запрос), проверяет лимит с учетом текущего резерва.
    Если генерация сегодня уже прошла, резерв равен 0!
    """
    used, _, limit = get_token_usage()
    
    if is_scheduled:
        if used >= limit:
            return False, "🛑 Превышен дневной лимит токенов (35 000). Рассылка приостановлена."
        return True, ""
    
    reserve = get_reserved_tokens()
    effective_limit = max(0, limit - reserve)
    
    if used >= effective_limit:
        if reserve > 0:
            reserve_str = f"{reserve:_}".replace("_", " ")
            return False, (
                f"🔒 Сегодня день авто-рассылки!\n"
                f"Зарезервировано {reserve_str} токенов под еще не отправленные отчеты (средний расход рассылок + 20%).\n"
                f"Личные запросы временно ограничены до завершения рассылки."
            )
        return False, "🛑 Превышен суточный лимит использования токенов (35 000). Запросы приостановлены до завтра."
    
    return True, ""

def get_token_stats_text() -> str:
    used, remaining, limit = get_token_usage()
    percent = min(100.0, (used / limit) * 100) if limit > 0 else 0
    
    bar_length = 10
    filled = int(bar_length * (used / limit)) if limit > 0 else 0
    progress_bar = "▓" * min(filled, bar_length) + "░" * (bar_length - min(filled, bar_length))

    used_str = f"{used:_}".replace("_", " ")
    limit_str = f"{limit:_}".replace("_", " ")
    rem_str = f"{remaining:_}".replace("_", " ")
    
    avg_sched = get_30_day_avg_scheduled_tokens()
    avg_sched_str = f"{avg_sched:_}".replace("_", " ")
    
    reserve = get_reserved_tokens()
    reserve_str = f"{reserve:_}".replace("_", " ")
    
    pending = get_pending_prompts_today()
    if pending:
        status_reserve = f"🔒 Резерв под невыполненную рассылку: {reserve_str} токенов\n"
    elif get_global_settings().get("completed_prompts_today"):
        status_reserve = "✅ Запланированная на сегодня рассылка уже выполнена (резерв снят)\n"
    else:
        status_reserve = "🔓 Сегодня обычный день (без резерва под рассылку)\n"

    return (
        f"🪙 Статистика расхода токенов Gemini AI\n\n"
        f"📊 Использовано сегодня: {used_str} / {limit_str}\n"
        f"💡 Осталось на сегодня: {rem_str}\n"
        f"📈 Прогресс: {progress_bar} ({percent:.1f}%)\n\n"
        f"📅 Средний расход на авто-отчеты (30 дней): {avg_sched_str} ток./день\n"
        f"{status_reserve}\n"
        f"ℹ️ Дневной лимит (35 000 токенов) обнуляется автоматически каждые сутки в 00:00."
    )
