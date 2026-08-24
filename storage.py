import json
import os
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MSK_TZ = ZoneInfo("Europe/Moscow")

DATA_DIR = os.getenv("DATA_DIR", "/app/data" if os.path.exists("/app/data") else "data")
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "data.json")
BAK_FILE = os.path.join(DATA_DIR, "data.json.bak")
TMP_FILE = os.path.join(DATA_DIR, "data.json.tmp")

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
    "- https://nfa.ru/ (NFA)\n"
    "- https://cbonds.ru (Cbonds)\n"    
    "- https://www.vedomosti.ru (Ведомости)\n"
    "- https://www.surgutneftegas.ru (Сургутнефтегаз)\n\n"
    "Формат ответа:\n"
    "- сначала краткий вывод;\n"
    "- затем расчётная и методологическая логика;\n"
    "- далее управленческие последствия и рекомендации;\n"
    "- используй таблицы для сравнения сценариев;\n"
    "- при недостатке исходных данных прямо укажи допущения и задай только критически важные уточняющие вопросы.\n\n"
    "Правила фактологической точности:\n"
    "Не выдумывай нормы, значения нормативов, номера документов и даты. При ссылках на регуляторные требования указывай точный номер, дату и официальное название документа.\n"
    "Категорически запрещено выдумывать URL-ссылки. Если точной прямой ссылки на конкретный документ нет в результатах веб-поиска — указывай только официальное наименование документа и главную страницу ведомства (например, cbr.ru или minfin.gov.ru).\n"
    "Запрещено использовать символы разметки Markdown (решетки #, звездочки *).\n\n"
    "Приоритет — подготовка готового решения для казначейства: что сделать, каким инструментом, в каком объёме, какой норматив или лимит будет ограничивающим и какие побочные эффекты возникнут.\n\n"
    "Контекст банка: ответ готовится для банка, отнесённого Банком России к системно значимым кредитным организациям (СЗКО). Учитывай применимые именно к СЗКО повышенные требования (в частности, надбавки к нормативам достаточности капитала, буфер за системную значимость, требования к ВЛА и планам восстановления финансовой устойчивости) и указывай, где это отличает подход СЗКО от прочих банков."
)

PROMPT_1 = (
    "Просканируй за последние 7 дней официальные ресурсы Банка России, Минфина России, "
    "Федерального Казначейства, Ассоциации банков Россия и рейтинговых агентств на предмет "
    "изменений регуляторных требований и официальных заявлений, важных для банковского казначея.\n\n"
    "Формат ответа:\n\n"
    "«Новые и изменённые нормативно-правовые акты» — список документов (название, номер, дата, кратко суть, ссылка), влияющих на: \n"
    "а) ликвидность (Н2–Н4, Н26, ВЛА); \n"
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

PROMPT_3 = (
    "Составь максимально полный и достоверный хронологический график плановых выплат (погашений и оферт) по корпоративным юаневым облигациям (CNY), торгующимся на Московской бирже (Мосбиржа).\n\n"
    "КРИТЕРИИ ОТБОРА:\n"
    "1. Валюта: CNY (китайский юань).\n"
    "2. Рынок: Российский локальный рынок (Мосбиржа).\n"
    "3. Минимальный объем эмиссии: Более 1 млрд CNY (по первоначальному или текущему номиналу).\n"
    "4. Период: начиная с сегодняшней даты на 18 месяцев вперед.\n\n"
    "ЖЕСТКИЕ ТРЕБОВАНИЯ К УЧЕТУ БУМАГ И СОБЫТИЙ (ВАЖНО!):\n"
    "1. Учитывай КАК плановые окончательные погашения, ТАК И ВСЕ оферты (как Put-оферты на выкуп, так и Call-оферты).\n"
    "2. ОБЯЗАТЕЛЬНО включай длинные выпуски крупнейших эмитентов (в частности, ПАО «НК «Роснефть»», ПАО «ГМК «Норильский никель»» и др.), у которых дата окончательного погашения находится в далёком будущем (2030–2035 гг.), НО промежуточная Put-оферта выпадает на запрашиваемый период.\n"
    "3. Перепроверь присутствие всех ключевых юаневых эмитентов: Роснефть, РУСАЛ, Норникель, Газпром нефть, Газпром капитал, Полюс, Металлоинвест, ФосАгро, ЭН+ ГИДРО, Группа Черкизово и др.\n"
    "4. ISIN-коды должны быть ТОЛЬКО локальными российскими кодами формата RU000A... (категорически запрещено использовать вымышленные европейские XS-коды).\n"
    "5. Если по выпуску ранее проводились оферты и часть объёма выкуплена, укажи первоначальный объем и сделай оговорку о фактическом объёме в обращении.\n\n"
    "ФОРМАТ ВЫВОДА:\n"
    "1. Сводная таблица со столбцами:\n"
    "   - Дата события (в формате ДД.ММ.ГГГГ)\n"
    "   - Тип события (Погашение / Оферта Put / Оферта Call)\n"
    "   - Эмитент и выпуск (Полное название, ISIN формата RU000A... и тикер на Мосбирже)\n"
    "   - Объем эмиссии (в млрд CNY)\n"
    "   - Текущий купон (% годовых и периодичность выплат)\n"
    "   - Дата окончательного погашения (заполняется, только если событие — оферта)\n\n"
    "2. Отсортируй таблицу строго по дате события от ближайших к дальним.\n\n"
    "3. Под таблицей приведи:\n"
    "   - Краткую сводную таблицу: общее количество событий и суммарный объем (в млрд CNY) отдельно по Погашениям и отдельно по Офертам (Put/Call).\n"
    "   - Краткий аналитический вывод о том, у каких эмитентов выпадают наибольшие пиковые объемы выплат и оферт в данный период."
)

PROMPT_4 = (
    "Подготовь ежемесячную справку для КУАП (Комитета по управлению активами и пассивами) о состоянии рублёвой и юаневой ликвидности за прошедший месяц.\n\n"
    "ФОРМАТ ОТВЕТА:\n\n"
    "1. «Резюме для КУАП» — 7 пронумерованных пунктов, каждый с пометкой в скобках (Факт / Оценка / Прогноз):\n"
    "   1) Текущее состояние рублёвого и юаневого секторов ликвидности (профицит/дефицит, масштаб в трлн руб. и оценка юаневого дефицита).\n"
    "   2) Динамика с начала месяца по обоим сегментам (изменение дефицита/профицита в рублях, изменение стоимости юаневого O/N-фондирования).\n"
    "   3) Основные драйверы рублёвой ликвидности (спрос на наличность, остатки банков на корсчетах в ЦБ, бюджетные потоки).\n"
    "   4) Основные драйверы юаневой ликвидности (операции Минфина по бюджетному правилу, лимиты валютных свопов ЦБ, корреспондентская инфраструктура).\n"
    "   5) Ключевые риски на ближайший месяц (пики налоговых платежей, волатильность базиса юаневых свопов, внешние ограничения).\n"
    "   6) Вероятная динамика ставок RUONIA и стоимости юаневого фондирования на горизонте месяца.\n"
    "   7) Конкретные рекомендации банку (управление подушкой ликвидности, лимиты по открытым CNY-позициям, действия казначейства).\n\n"
    "2. «Таблица ключевых индикаторов» со столбцами: Показатель | Значение на начало месяца (дата) | Последнее значение (дата) | Изменение | Интерпретация.\n"
    "   Обязательно включи как минимум: RUONIA (% годовых), структурный дефицит/профицит RUB (трлн руб.), ставку юаневого свопа CNY_TODTOM / RUSFAR CNY (% годовых), официальный курс CNY/RUB.\n\n"
    "- Указывай точные цифры и даты, избегай общих формулировок без конкретики.\n"
    "- Пиши сухим аналитическим языком, без вводной «воды».\n"
    "- Ответ готовится для системно значимой кредитной организации (СЗКО) — учитывай масштаб операций и повышенные регуляторные требования, применимые именно к СЗКО."
)

DEFAULT_DATA = {
    "settings": {
        "prompt_1": PROMPT_1,
        "day_1": 4,       # Пятница (день недели 0..6)
        "time_1": "13:30",
        "prompt_2": PROMPT_2,
        "day_2": 1,       # Вторник (день недели 0..6)
        "time_2": "14:00",
        "prompt_3": PROMPT_3,
        "day_3": 1,       # 1-е число месяца (1..28)
        "time_3": "15:00",
        "prompt_4": PROMPT_4,
        "day_4": 5,       # 5-е число месяца (1..28)
        "time_4": "16:00",
        "master_prompt": DEFAULT_MASTER_PROMPT,
        "timezone": "Europe/Moscow",
        "is_paused": False,
        "token_last_date": str(date.today()),
        "tokens_used_today": 0,
        "token_history": {},
        "scheduled_runs": [],
        "completed_prompts_today": []
    },
    "users": [],
    "users_info": {}
}


def _ensure_default_keys(data: dict):
    data.setdefault("settings", {})
    data.setdefault("users", [])
    data.setdefault("users_info", {})
    for k, v in DEFAULT_DATA["settings"].items():
        if k not in data["settings"]:
            data["settings"][k] = v

def load_data() -> dict:
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
        logging.error(f"Ошибка при загрузке {DATA_FILE}: {e}. Восстановление из бэкапа...")
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
    try:
        with open(TMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(DATA_FILE):
            os.replace(DATA_FILE, BAK_FILE)
        
        os.replace(TMP_FILE, DATA_FILE)
    except Exception as e:
        logging.error(f"Ошибка при сохранении {DATA_FILE}: {e}")

def _get_msk_today_str() -> str:
    """Текущая дата по московскому времени (МСК), независимо от часового пояса сервера."""
    return str(datetime.now(MSK_TZ).date())

def _reset_daily_tokens_if_needed(data: dict) -> dict:
    """Ленивый сброс: срабатывает при любом обращении к настройкам, если по МСК уже наступили новые сутки."""
    today_str = _get_msk_today_str()
    if data["settings"].get("token_last_date") != today_str:
        data["settings"]["token_last_date"] = today_str
        data["settings"]["tokens_used_today"] = 0
        data["settings"]["completed_prompts_today"] = []
        save_data(data)
    return data

def force_reset_daily_tokens():
    """Принудительный сброс суточного лимита токенов. Вызывается планировщиком строго в 00:00 по МСК."""
    data = load_data()
    today_str = _get_msk_today_str()
    data["settings"]["token_last_date"] = today_str
    data["settings"]["tokens_used_today"] = 0
    data["settings"]["completed_prompts_today"] = []
    save_data(data)
    logging.info("🔄 Суточный лимит токенов сброшен по расписанию (00:00 МСК).")

def get_global_settings() -> dict:
    data = load_data()
    data = _reset_daily_tokens_if_needed(data)
    return data.get("settings", {})

def update_global_settings(new_settings: dict):
    data = load_data()
    data["settings"].update(new_settings)
    save_data(data)

def sync_prompts_from_code():
    data = load_data()
    data["settings"]["prompt_1"] = PROMPT_1
    data["settings"]["prompt_2"] = PROMPT_2
    data["settings"]["prompt_3"] = PROMPT_3
    data["settings"]["prompt_4"] = PROMPT_4
    data["settings"]["master_prompt"] = DEFAULT_MASTER_PROMPT
    save_data(data)
    logging.info("📝 Промпты и Мастер-промпт успешно синхронизированы из исходного кода.")
def register_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    data = load_data()
    if user_id not in data.get("users", []):
        data.setdefault("users", []).append(user_id)

    info = data.setdefault("users_info", {})
    entry = info.get(str(user_id), {})
    if username is not None:
        entry["username"] = username
    if first_name is not None:
        entry["first_name"] = first_name
    if last_name is not None:
        entry["last_name"] = last_name
    if "subscribed_at" not in entry:
        entry["subscribed_at"] = str(date.today())
    info[str(user_id)] = entry
    data["users_info"] = info

    save_data(data)

def unregister_user(user_id: int):
    data = load_data()
    users = data.get("users", [])
    if user_id in users:
        users.remove(user_id)
        data["users"] = users
        save_data(data)

def is_user_registered(user_id: int) -> bool:
    data = load_data()
    return user_id in data.get("users", [])

def get_registered_users() -> list:
    data = load_data()
    return data.get("users", [])

def get_subscribers_info() -> list[dict]:
    data = load_data()
    users = data.get("users", [])
    info = data.get("users_info", {})
    result = []
    for uid in users:
        meta = info.get(str(uid), {})
        result.append({
            "id": uid,
            "username": meta.get("username"),
            "first_name": meta.get("first_name"),
            "last_name": meta.get("last_name"),
            "subscribed_at": meta.get("subscribed_at"),
        })
    return result

def add_token_usage(tokens: int, is_scheduled: bool = False):
    data = load_data()
    today_str = str(date.today())
    
    current_used = data["settings"].get("tokens_used_today", 0)
    data["settings"]["tokens_used_today"] = current_used + tokens
    
    history = data["settings"].setdefault("token_history", {})
    history[today_str] = history.get(today_str, 0) + tokens

    if is_scheduled:
        runs = data["settings"].setdefault("scheduled_runs", [])
        runs.append({"date": today_str, "tokens": tokens})
    
    cutoff_date = date.today() - timedelta(days=30)
    data["settings"]["token_history"] = {
        d: val for d, val in history.items()
        if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff_date
    }
    if "scheduled_runs" in data["settings"]:
        data["settings"]["scheduled_runs"] = [
            r for r in data["settings"]["scheduled_runs"]
            if datetime.strptime(r["date"], "%Y-%m-%d").date() >= cutoff_date
        ]
    
    save_data(data)

def mark_prompt_completed_today(prompt_num: int):
    data = load_data()
    data = _reset_daily_tokens_if_needed(data)
    completed = data["settings"].get("completed_prompts_today", [])
    if prompt_num not in completed:
        completed.append(prompt_num)
        data["settings"]["completed_prompts_today"] = completed
        save_data(data)

def get_30_day_avg_scheduled_tokens() -> int:
    settings = get_global_settings()
    runs = settings.get("scheduled_runs", [])
    if runs:
        total = sum(r.get("tokens", 0) for r in runs)
        return int(total / len(runs)) if len(runs) > 0 else 0
    return 0

def get_pending_prompts_today() -> list[int]:
    settings = get_global_settings()
    today = date.today()
    today_weekday = today.weekday()
    today_day_of_month = today.day
    
    d1 = settings.get("day_1", 4) % 7
    d2 = settings.get("day_2", 1) % 7
    d3 = settings.get("day_3", 1)
    d4 = settings.get("day_4", 5)
    
    completed = settings.get("completed_prompts_today", [])
    pending = []

    if today_weekday == d1 and 1 not in completed:
        pending.append(1)
    if today_weekday == d2 and 2 not in completed:
        pending.append(2)
    if today_day_of_month == d3 and 3 not in completed:
        pending.append(3)
    if today_day_of_month == d4 and 4 not in completed:
        pending.append(4)

    return pending

def get_reserved_tokens() -> int:
    pending = get_pending_prompts_today()
    if not pending:
        return 0
    avg_per_prompt = get_30_day_avg_scheduled_tokens()
    return int(len(pending) * (avg_per_prompt or 10000) * 1.25)

def get_token_usage() -> tuple[int, int, int]:
    settings = get_global_settings()
    used = settings.get("tokens_used_today", 0)
    remaining = max(0, DAILY_TOKEN_LIMIT - used)
    return used, remaining, DAILY_TOKEN_LIMIT

def check_token_limit(is_scheduled: bool = False) -> tuple[bool, str]:
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
                f"Зарезервировано {reserve_str} токенов под еще не отправленные отчеты (средний расход промпта + 25%).\n"
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
        status_reserve = f"🔒 Резерв токенов для рассылки: {reserve_str} токенов\n"
    elif get_global_settings().get("completed_prompts_today"):
        status_reserve = "✅ Запланированная на сегодня рассылка уже выполнена (резерв снят)\n"
    else:
        status_reserve = "🔓 Сегодня обычный день (без резерва под рассылку)\n"

    return (
        f"🪙 Статистика расхода токенов Gemini AI\n\n"
        f"📊 Использовано сегодня: {used_str} / {limit_str}\n"
        f"💡 Осталось на сегодня: {rem_str}\n"
        f"📈 Прогресс: {progress_bar} ({percent:.1f}%)\n\n"
        f"📅 Средний расход на авто-отчет (30 дней): {avg_sched_str} ток./промпт\n"
        f"{status_reserve}\n"
        f"ℹ️ Дневной лимит (35 000 токенов) обнуляется автоматически каждые сутки в 00:00 по московскому времени (МСК)."
    )
