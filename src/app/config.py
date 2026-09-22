import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str):
    """ID из .env как int; None, если переменная не задана или не число."""
    value = os.getenv(name)
    if not value:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def _int_list_env(name: str) -> list:
    """Список ID из .env через запятую (пустой список, если не задано)."""
    value = os.getenv(name, "")
    return [int(p) for p in (x.strip() for x in value.split(",")) if p.isdigit()]


TOKEN = os.getenv("TOKEN")
DB_PATH = os.path.join(os.path.dirname(__file__), "database", "database.db")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

# ---------------------------------------------------------------------------
# ID всех объектов сервера задаются через .env (см. .env.example).
# Бот работает по ID; имя используется только как запасной вариант,
# если соответствующий ID не задан.
# ---------------------------------------------------------------------------

# Категория для создания тикетов
TICKETS_CATEGORY_NAME = "𝙄𝙣𝙫𝙖𝙞𝙩 𝙁𝙖𝙢𝙞𝙡𝙮"
TICKETS_CATEGORY_ID = _int_env("TICKETS_CATEGORY_ID")

# Роли
ROLE_APPLIED = "Подал заявку"  # выдается при создание тикета
ROLE_APPLIED_ID = _int_env("ROLE_APPLIED_ID")
ROLE_RECRUITER = "𝐑𝐞𝐜𝐫𝐮𝐢𝐭👨🏻‍💻"
ROLE_RECRUITER_ID = _int_env("ROLE_RECRUITER_ID")
ROLE_OWNER = "𝙊𝙬𝙣𝙚𝙧👑"
ROLE_OWNER_ID = _int_env("ROLE_OWNER_ID")
ROLE_DEP_OWNER = "𝘿𝙚𝙥.O𝙬𝙣𝙚𝙧⭐"
ROLE_DEP_OWNER_ID = _int_env("ROLE_DEP_OWNER_ID")
ROLE_ADMIN = "Admin"
ROLE_ADMIN_ID = _int_env("ROLE_ADMIN_ID")
ROLE_SUPPORT = "Support"
ROLE_SUPPORT_ID = _int_env("ROLE_SUPPORT_ID")

# ID стафф-ролей (без None) — права на модерацию AFK и доступ к лог-каналу
STAFF_ROLE_IDS = [
    role_id
    for role_id in (
        ROLE_RECRUITER_ID,
        ROLE_OWNER_ID,
        ROLE_DEP_OWNER_ID,
        ROLE_ADMIN_ID,
        ROLE_SUPPORT_ID,
    )
    if role_id
]

# Каналы
LOG_CHANNEL_NAME = "📋ᥙᴛ᧐ᴦᥙ-ɜᥲяʙ᧐κ"
LOG_CHANNEL_ID = _int_env("LOG_CHANNEL_ID")
VOICE_CHANNELS = ["🔊Обзвон 1", "🔊Обзвон 2", "🔊Обзвон 3"]
VOICE_CHANNEL_IDS = _int_list_env("VOICE_CHANNEL_IDS")

# Лог-центр: один канал, внутри — ветки по категориям.
# ID веток можно задать в .env; иначе бот сам создаст их по именам.
LOG_KEY_TICKETS = "tickets"  # новые заявки
LOG_KEY_DECISIONS = "decisions"  # решения по заявкам
LOG_KEY_AFK = "afk"  # установка/снятие AFK
LOG_KEY_CALLS = "calls"  # вызовы на обзвон
LOG_KEY_STATS = "stats"  # статистика
LOG_KEY_ERRORS = "errors"  # ошибки бота

LOG_THREAD_NAMES = {
    LOG_KEY_TICKETS: "📝-заявки",
    LOG_KEY_DECISIONS: "⚖️-решения",
    LOG_KEY_AFK: "🔴-afk",
    LOG_KEY_CALLS: "🔊-обзвоны",
    LOG_KEY_STATS: "📊-статистика",
    LOG_KEY_ERRORS: "🚨-ошибки",
}

LOG_THREAD_IDS = {
    LOG_KEY_TICKETS: _int_env("LOG_THREAD_TICKETS_ID"),
    LOG_KEY_DECISIONS: _int_env("LOG_THREAD_DECISIONS_ID"),
    LOG_KEY_AFK: _int_env("LOG_THREAD_AFK_ID"),
    LOG_KEY_CALLS: _int_env("LOG_THREAD_CALLS_ID"),
    LOG_KEY_STATS: _int_env("LOG_THREAD_STATS_ID"),
    LOG_KEY_ERRORS: _int_env("LOG_THREAD_ERRORS_ID"),
}

# Команды
CMD_PREFIX = "!"
CMD_REGENT = "regent"
CMD_STATS = "stats"
CMD_HISTORY = "history"
CMD_AFK_REMOVE = "afk_remove"
CMD_DELETE_USER_DATA = "delete_user_data"

# Rate limits (commands.cooldown): значения в секундах.
REGENT_COMMAND_COOLDOWN_SECONDS = 30
AFK_COMMAND_COOLDOWN_SECONDS = 10
AFK_LIST_COOLDOWN_SECONDS = 10
AFK_LOOKUP_COOLDOWN_SECONDS = 10
TICKET_BUTTON_COOLDOWN_SECONDS = 5

# Тексты
DM_MESSAGE = "Вы подали заявку в клуб Regent, ожидайте — скоро её рассмотрят ⏳."

TICKET_RP_TITLE = "RP ЗАЯВКА"
TICKET_CAPT_TITLE = "CAPT ЗАЯВКА"

REGENT_EMBED_TITLE = "Regent FamQ"
PRIVACY_NOTICE = (
    "Отправляя заявку, вы соглашаетесь, что ответы формы и сообщения тикета "
    "сохраняются для рассмотрения администрацией этого Discord-сервера. "
    "Удаление или анонимизацию можно запросить у администратора."
)

REGENT_EMBED_DESCRIPTION = (
    "Заявка в Regent FamQ\n\n"
    "Заявки рассматриваются только на сервере Orlando.\n"
    "Уведомление о приглашении на обзвон отправляется в ваш тикет.\n\n"
    "**Срок рассмотрения:** до 48 часов.\n"
    "**Важно:** неполная заявка будет автоматически ОТКЛОНЕНА.\n\n"
    "**Требования к откатам:**\n"
    "Арена — не более 1 недели (не менее 5 минут).\n"
    "Откаты каптов и MCL — не более 60 дней (по желанию).\n"
    "**Нарушение условий = автоматический отказ.**\n\n"
    f"**Данные:** {PRIVACY_NOTICE}"
)
RP_FIELDS = [
    ("Никнейм в игре + статик", "Ваш игровой ник и статик", True, 100),
    ("OOC имя и возраст(IRL)", "Ваше реальное имя и возраст", True, 100),
    ("Семьи в которых вы состояли", "Перечислите все семьи, и почему ушли?", True, 300),
    ("Почему именно наша семья", "Потому что ...", True, 500),
    (
        "Средний онлайн в день (пример: 12:00-17:00)",
        "Сколько часов играете / в какое время",
        True,
        100,
    ),
]

CAPT_FIELDS = [
    ("Никнейм в игре", "Ваш игровой ник", True, 50),
    ("Статик", "Ваш статик", False, 50),
    ("OOC имя и возраст", "Ваше реальное имя и возраст", True, 100),
    (
        "Откат сайга и спешик",
        "Ваши откаты (важно: нужно именно два ваших отката)",
        True,
        200,
    ),
    (
        "Откаты MCL каптов и МП",
        "Ваши откаты в MCL (необязательно, но будет плюсом)",
        False,
        200,
    ),
]

ACCEPT_EMBED_TITLE = "✅ Заявка принята, добро пожаловать в семью"
DENY_EMBED_TITLE = "❌ Заявка отклонена"

ERROR_TICKET_CREATE = "Не удалось создать заявку. Попробуйте позже."

# Тикеты: модерация и уведомления заявителя
TICKET_NO_PERMISSION = "⛔ Обрабатывать заявки могут только модераторы."
TICKET_ALREADY_DECIDED = "⚠️ Этот тикет уже обработан."
TICKET_ALREADY_OPEN = "⚠️ У вас уже есть открытая заявка: {channel}"
TICKET_CLOSED_LOG_TITLE = "🔒 Тикет закрыт"
DM_TICKET_ACCEPTED = "🎉 Ваша заявка принята! Добро пожаловать в семью."
DM_TICKET_DENIED = "❌ Ваша заявка отклонена. Причина: {reason}"
DM_TICKET_CLOSED = "🔒 Ваш тикет закрыт модератором. Если вопрос остался — создайте новый."

# AFK Система
AFK_EMBED_TITLE = "🔴 AFK Система"
AFK_EMBED_DESCRIPTION = "Используй кнопки ниже для управления статусом AFK"
AFK_MENU_TITLE = "Во время AFK вам не будут выдавать высказывания по причине НВС"
AFK_MENU_NO_AFK = "В АФК никого нет."
AFK_MENU_TOTAL = "Всего в АФК"

# установка AFK
AFK_MODAL_TITLE = "Установка AFK"
AFK_MODAL_REASON_LABEL = "📝 Причина"
AFK_MODAL_REASON_PLACEHOLDER = "🔴 Взять AFK"
AFK_MODAL_DURATION_LABEL = "⏰ На сколько?"
AFK_MODAL_DURATION_PLACEHOLDER = "1 час, 2 часа, 3 часа, 23:45, через 30 мин"

AFK_RETURN_MODAL_TITLE = "Возвращение из AFK"

AFK_BUTTON_LEAVE = "🔴 Взять AFK"
AFK_BUTTON_RETURN = "🟢 Отменить AFK"
AFK_BUTTON_REFRESH = "📋 Список AFK"
AFK_BUTTON_STAY = "❌ Остаюсь"

AFK_REASON_DEFAULT = "🔴 Взять AFK"

AFK_RETURN_CONFIRM = "Вы уверены, что хотите снять AFK статус?"
AFK_RETURN_DURATION_LABEL = "Ты отсутствовал"

AFK_RETURN_SUCCESS = "✅ Вы вернулись!"
AFK_RETURN_STAY = "❌ Вы остались в AFK."
AFK_RETURN_ERROR = "Ошибка: AFK статус не найден."
AFK_NOT_AFK = "Вы не находитесь в AFK."
AFK_INVALID_USER = "Это не ваше меню."

AFK_AFK_STATUS = "🔴 В АФК"
AFK_CHECKED_NOT_AFK = "✅ Этот пользователь не в AFK"

AFK_STATS_TITLE = "📊 AFK Статистика"
AFK_STATS_NO_DATA = "📊 Статистика AFK: пользователь ещё не использовал AFK"

AFK_STATS_TOTAL = "Всего уходов в AFK"
AFK_STATS_TOTAL_TIME = "Общее время в AFK"
AFK_STATS_LONGEST = "Самая долгая сессия"

AFK_AUTO_REPLY = "{mention} **в AFK**\nПричина: {reason}\nУшёл: {duration} назад"

AFK_VOICE_RETURN_TITLE = "{user} вернулся!"
AFK_VOICE_RETURN_DESC = "🟢 Пользователь вернулся из AFK (вошёл в голосовой канал)"

AFK_COOLDOWN_SECONDS = 30
AFK_NICK_PREFIX = "[AFK] "

# Авто-снятие AFK по истечении времени (фоновая задача)
AFK_EXPIRY_CHECK_SECONDS = int(os.getenv("AFK_EXPIRY_CHECK_SECONDS", "60"))
AFK_EXPIRED_DM = "⏰ Ваш AFK на сервере {guild} истёк — вы снова в строю."

# Логи AFK в лог-центр
AFK_LOG_SET_TITLE = "🔴 AFK установлен"
AFK_LOG_REMOVED_TITLE = "🟢 AFK снят"
AFK_LOG_EXPIRED_TITLE = "⏰ AFK истёк"

# Модераторское снятие AFK (команда !afk_remove)
AFK_NO_PERMISSION = "⛔ Снимать AFK у других могут только модераторы."
AFK_GUILD_ONLY = "AFK-меню работает только на сервере."
PRIVACY_DELETE_DONE = (
    "✅ Данные пользователя очищены: тикеты анонимизированы — {tickets}, "
    "AFK-записи — {afk_users}, AFK-статистика — {afk_stats}, кулдауны — {afk_cooldown}."
)

# Поля эмбеда !afk_check
AFK_FIELD_STATUS = "Статус"
AFK_FIELD_REASON = "Причина"
AFK_FIELD_LEFT = "Ушёл"
AFK_FIELD_DURATION = "Время в AFK"


def validate() -> list:
    """Проверяет конфиг по жёстким лимитам Discord перед запуском.

    Возвращает список найденных ошибок (пустой = всё ок).
    """
    errors = []

    for name, fields in (("RP_FIELDS", RP_FIELDS), ("CAPT_FIELDS", CAPT_FIELDS)):
        if len(fields) > 5:
            errors.append(f"{name}: полей {len(fields)}, а модалка вмещает максимум 5")
        for label, *_ in fields:
            if len(label) > 45:
                errors.append(f"{name}: label «{label[:30]}…» длиной {len(label)} > 45 символов")

    if not TICKET_RP_TITLE or not TICKET_CAPT_TITLE:
        errors.append("Заголовки форм заявок не должны быть пустыми")

    if AFK_EXPIRY_CHECK_SECONDS < 10:
        errors.append("AFK_EXPIRY_CHECK_SECONDS слишком мал (< 10 сек)")

    return errors
