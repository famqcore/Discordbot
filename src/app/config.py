"""Конфигурация бота: загрузка, разбор и валидация .env.

Три раздельных шага:

1. **Загрузка.** ``load_dotenv()`` и чтение переменных окружения как строк.
2. **Разбор.** Функции ``_int_env``/``_int_list_env``/``_seconds_env`` строго
   различают «значение не задано» (берётся документированное значение по
   умолчанию) и «значение задано, но неверно» (ошибка в отчёт, без тихого
   приведения к None). Разбор никогда не бросает исключение на импорте —
   иначе оператор получает traceback вместо списка исправлений.
3. **Валидация.** ``validate()`` собирает все проблемы в один отчёт:
   диапазоны интервалов, положительность и уникальность ID, соответствие
   списка голосовых каналов подписям, лимиты Discord, запрет небезопасных
   production-fallback.

Секрет TOKEN никогда не попадает в текст ошибки или лога: сообщения
оперируют только именем переменной.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Ошибки разбора .env, собранные при импорте. validate() докладывает их до старта.
_ENV_ERRORS: list[str] = []

# Discord snowflake: 64-битный ID, на практике 17-20 цифр.
MIN_DISCORD_ID = 10**16
MAX_DISCORD_ID = 2**63 - 1


def _raw_env(name: str) -> str | None:
    """Значение переменной окружения без пробелов; None, если не задано."""
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _error(message: str) -> None:
    if message not in _ENV_ERRORS:
        _ENV_ERRORS.append(message)


def _int_env(name: str) -> int | None:
    """Discord ID из .env; None, если переменная не задана.

    Нечисловое значение или значение вне диапазона snowflake — ошибка
    конфигурации: в .env нельзя написать «какой-нибудь id» и получить
    молчаливую подмену поведения.
    """
    value = _raw_env(name)
    if value is None:
        return None
    if not value.isdigit():
        _error(f"{name}: значение «{value}» не похоже на Discord ID (нужны только цифры)")
        return None
    number = int(value)
    if number < MIN_DISCORD_ID or number > MAX_DISCORD_ID:
        _error(
            f"{name}: значение {number} вне диапазона Discord ID "
            f"({MIN_DISCORD_ID}–{MAX_DISCORD_ID})"
        )
        return None
    return number


def _bool_env(name: str, default: bool = False) -> bool:
    value = _raw_env(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    _error(f"{name}: значение «{value}» не булево (ожидается true/false)")
    return default


def _int_list_env(name: str) -> list[int]:
    """Список Discord ID через запятую (пустой список, если не задано).

    Нечисловые элементы — ошибка конфигурации, а не повод их пропустить:
    оператор думает, что ID настроен, а бот его не видит. Дубликаты тоже
    ошибка: одна и та же кнопка обзвона не может вести в два канала.
    """
    value = os.getenv(name, "")
    ids: list[int] = []
    for part in (item.strip() for item in value.split(",")):
        if not part:
            continue
        if not part.isdigit():
            _error(f"{name}: элемент «{part}» не похож на Discord ID (нужны только цифры)")
            continue
        number = int(part)
        if number < MIN_DISCORD_ID or number > MAX_DISCORD_ID:
            _error(f"{name}: элемент {number} вне диапазона Discord ID")
            continue
        if number in ids:
            _error(f"{name}: ID {number} указан несколько раз")
            continue
        ids.append(number)
    return ids


@dataclass(frozen=True)
class IntBounds:
    minimum: int
    maximum: int
    unit: str


def _bounded_int_env(name: str, default: int, bounds: IntBounds) -> int:
    """Целое значение из .env в заданных границах; при ошибке — default."""
    value = _raw_env(name)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        _error(f"{name}: значение «{value}» не целое число ({bounds.unit})")
        return default
    if not bounds.minimum <= number <= bounds.maximum:
        _error(
            f"{name}: {number} вне допустимого диапазона "
            f"{bounds.minimum}–{bounds.maximum} {bounds.unit}"
        )
        return default
    return number


def _timezone_env(name: str, default: str) -> str:
    """Имя часового пояса IANA (например Europe/Moscow)."""
    from utils.clock import is_valid_timezone

    value = _raw_env(name)
    if value is None:
        return default
    if not is_valid_timezone(value):
        _error(f"{name}: «{value}» не является именем часового пояса IANA (пример: Europe/Moscow)")
        return default
    return value


TOKEN = _raw_env("TOKEN")
DB_PATH = os.path.join(os.path.dirname(__file__), "database", "database.db")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3

# ---------------------------------------------------------------------------
# ID всех объектов сервера задаются через .env (см. .env.example).
# Бот работает строго по ID: имя никогда не заменяет заданный ID, а объект
# без ID либо создаётся ботом приватным, либо безопасно пропускается.
#
# ALLOW_NAME_FALLBACK — небезопасный режим разработки: включает поиск
# ролей/каналов по имени и переводит проверки инфраструктуры из фатальных
# в предупреждения. НИКОГДА не включайте в production: имена в Discord
# не уникальны, одноимённая роль может получить доступ к тикетам и логам.
# ---------------------------------------------------------------------------

ALLOW_NAME_FALLBACK = _bool_env("ALLOW_NAME_FALLBACK", False)

# production (по умолчанию) запрещает небезопасные dev-режимы; development
# разрешает их явным выбором оператора.
ENVIRONMENT = (_raw_env("ENVIRONMENT") or "production").lower()

# Категория для создания тикетов
TICKETS_CATEGORY_NAME = "FAMQCORE • заявки"
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
LOG_KEY_AUDIT = "audit"  # аудит: удаление данных, ретенция

LOG_THREAD_NAMES = {
    LOG_KEY_TICKETS: "📝-заявки",
    LOG_KEY_DECISIONS: "⚖️-решения",
    LOG_KEY_AFK: "🔴-afk",
    LOG_KEY_CALLS: "🔊-обзвоны",
    LOG_KEY_STATS: "📊-статистика",
    LOG_KEY_ERRORS: "🚨-ошибки",
    LOG_KEY_AUDIT: "🧾-аудит",
}

LOG_THREAD_IDS = {
    LOG_KEY_TICKETS: _int_env("LOG_THREAD_TICKETS_ID"),
    LOG_KEY_DECISIONS: _int_env("LOG_THREAD_DECISIONS_ID"),
    LOG_KEY_AFK: _int_env("LOG_THREAD_AFK_ID"),
    LOG_KEY_CALLS: _int_env("LOG_THREAD_CALLS_ID"),
    LOG_KEY_STATS: _int_env("LOG_THREAD_STATS_ID"),
    LOG_KEY_ERRORS: _int_env("LOG_THREAD_ERRORS_ID"),
    LOG_KEY_AUDIT: _int_env("LOG_THREAD_AUDIT_ID"),
}

# Обратная связь ключ → имя переменной окружения (для сообщений об ошибках)
LOG_THREAD_ENV_NAMES = {
    LOG_KEY_TICKETS: "LOG_THREAD_TICKETS_ID",
    LOG_KEY_DECISIONS: "LOG_THREAD_DECISIONS_ID",
    LOG_KEY_AFK: "LOG_THREAD_AFK_ID",
    LOG_KEY_CALLS: "LOG_THREAD_CALLS_ID",
    LOG_KEY_STATS: "LOG_THREAD_STATS_ID",
    LOG_KEY_ERRORS: "LOG_THREAD_ERRORS_ID",
    LOG_KEY_AUDIT: "LOG_THREAD_AUDIT_ID",
}

# Команды
CMD_PREFIX = "!"
CMD_FAMQCORE = "famqcore"
CMD_STATS = "stats"
CMD_HISTORY = "history"
CMD_AFK = "afk"
CMD_AFK_LIST = "afk_list"
CMD_AFK_CHECK = "afk_check"
CMD_AFK_STATS = "afk_stats"
CMD_AFK_REMOVE = "afk_remove"
CMD_DELETE_USER_DATA = "delete_user_data"

# Rate limits (commands.cooldown): число вызовов и интервал в секундах.
FAMQCORE_COMMAND_COOLDOWN_RATE = 1
FAMQCORE_COMMAND_COOLDOWN_SECONDS = 30
AFK_COMMAND_COOLDOWN_RATE = 1
AFK_COMMAND_COOLDOWN_SECONDS = 10
AFK_LIST_COOLDOWN_RATE = 1
AFK_LIST_COOLDOWN_SECONDS = 10
AFK_LOOKUP_COOLDOWN_RATE = 3
AFK_LOOKUP_COOLDOWN_SECONDS = 10
TICKET_ADMIN_COMMAND_COOLDOWN_RATE = 2
TICKET_ADMIN_COMMAND_COOLDOWN_SECONDS = 10
TICKET_BUTTON_COOLDOWN_SECONDS = 5
VOICE_CALL_BUTTON_COOLDOWN_SECONDS = 15
TICKET_HISTORY_DEFAULT_LIMIT = 10
TICKET_HISTORY_MIN_LIMIT = 1
TICKET_HISTORY_MAX_LIMIT = 25
DELETE_USER_DATA_CONFIRM_TIMEOUT_SECONDS = 60
VOICE_SELECT_VIEW_TIMEOUT_SECONDS = 60
AFK_RETURN_VIEW_TIMEOUT_SECONDS = 60

# Тексты
DM_MESSAGE = "Вы подали заявку в FAMQCORE, ожидайте — скоро её рассмотрят ⏳."

FAMQCORE_EMBED_TITLE = "FAMQCORE"
PRIVACY_NOTICE = (
    "Отправляя заявку, вы соглашаетесь, что ответы формы и сообщения тикета "
    "сохраняются для рассмотрения администрацией этого Discord-сервера. "
    "Удаление или анонимизацию можно запросить у администратора."
)

FAMQCORE_EMBED_DESCRIPTION = (
    "Добро пожаловать в FAMQCORE.\n\n"
    "Выберите тип заявки и заполните форму — команда рассмотрит её в приватном тикете.\n"
    "Приглашение на обзвон, решение и дополнительная информация появятся в вашем тикете.\n\n"
    "**Важно:** заполняйте форму внимательно и указывайте актуальные данные.\n\n"
    f"**Данные:** {PRIVACY_NOTICE}"
)


@dataclass(frozen=True)
class TicketField:
    label: str
    placeholder: str
    required: bool
    max_length: int


@dataclass(frozen=True)
class TicketForm:
    ticket_type: str
    title: str
    fields: tuple[TicketField, ...]


RP_FORM = TicketForm(
    ticket_type="rp",
    title="RP ЗАЯВКА",
    fields=(
        TicketField("Никнейм в игре + статик", "Ваш игровой ник и статик", True, 100),
        TicketField("OOC имя и возраст(IRL)", "Ваше реальное имя и возраст", True, 100),
        TicketField(
            "Семьи в которых вы состояли", "Перечислите все семьи, и почему ушли?", True, 300
        ),
        TicketField("Почему именно наша семья", "Потому что ...", True, 500),
        TicketField(
            "Средний онлайн (например, 12:00–17:00)",
            "Сколько часов играете / в какое время",
            True,
            100,
        ),
    ),
)

CAPT_FORM = TicketForm(
    ticket_type="capt",
    title="CAPT ЗАЯВКА",
    fields=(
        TicketField("Никнейм в игре", "Ваш игровой ник", True, 50),
        TicketField("Статик", "Ваш статик", False, 50),
        TicketField("OOC имя и возраст", "Ваше реальное имя и возраст", True, 100),
        TicketField(
            "Откат сайга и спешик",
            "Ваши откаты (важно: нужно именно два ваших отката)",
            True,
            200,
        ),
        TicketField(
            "Откаты MCL каптов и МП",
            "Ваши откаты в MCL (необязательно, но будет плюсом)",
            False,
            200,
        ),
    ),
)

TICKET_FORMS = (RP_FORM, CAPT_FORM)
TICKET_RP_TITLE = RP_FORM.title
TICKET_CAPT_TITLE = CAPT_FORM.title

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
AFK_REASON_INPUT_MAX_LENGTH = 100
AFK_DURATION_INPUT_MAX_LENGTH = 50

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
AFK_RETURN_EXPIRED = (
    "⌛ Время подтверждения истекло. Ваш AFK-статус не изменён. Откройте меню заново."
)
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
DISCORD_NICK_MAX_LENGTH = 32

# Границы длительности AFK. Нулевая длительность запрещена:
# «остаюсь на месте» — это не AFK, а кнопка «Отменить AFK».
AFK_MIN_DURATION_MINUTES = 1
AFK_MAX_DURATION_MINUTES = 30 * 24 * 60  # 30 суток

# Автоответы на упоминания AFK
AFK_MAX_MENTIONS_PER_MESSAGE = 5  # сколько упоминаний проверяем в одном сообщении
AFK_REPLY_CHANNEL_LIMIT = 3  # автоответов на канал за окно
AFK_REPLY_CHANNEL_WINDOW_SECONDS = 20
AFK_REPLY_DELETE_AFTER_SECONDS = 60

# Авто-снятие AFK по истечении времени (фоновая задача)
AFK_EXPIRY_CHECK_SECONDS = _bounded_int_env(
    "AFK_EXPIRY_CHECK_SECONDS", 60, IntBounds(10, 86400, "сек")
)
AFK_EXPIRED_DM = "⏰ Ваш AFK на сервере {guild} истёк — вы снова в строю."

# Логи AFK в лог-центр
AFK_LOG_SET_TITLE = "🔴 AFK установлен"
AFK_LOG_REMOVED_TITLE = "🟢 AFK снят"
AFK_LOG_EXPIRED_TITLE = "⏰ AFK истёк"

# Модераторское снятие AFK (команда !afk_remove)
AFK_NO_PERMISSION = "⛔ Снимать AFK у других могут только модераторы."
AFK_GUILD_ONLY = "AFK-меню работает только на сервере."

# Удаление персональных данных (!delete_user_data)
PRIVACY_DELETE_CONFIRM = (
    "⚠️ Удалить персональные данные {member} на этом сервере?\n\n"
    "Будет выполнено, без возможности отката:\n"
    "• открытый тикет и его канал с перепиской — удаляются;\n"
    "• тикеты анонимизируются (ответы формы, имя и ID заявителя стираются);\n"
    "• сохранённые сообщения лог-центра по этим тикетам (включая транскрипты) — удаляются;\n"
    "• AFK-статус, AFK-статистика и кулдауны автоответов — удаляются.\n\n"
    "Останется: агрегированная статистика без персональных данных, "
    "записи аудита административных действий и копии в резервных бэкапах "
    "до их ротации (см. docs/privacy.md)."
)
PRIVACY_DELETE_CONFIRM_BUTTON = "🗑 Да, удалить данные"
PRIVACY_DELETE_CANCEL_BUTTON = "Отмена"
PRIVACY_DELETE_CANCELLED = "Удаление данных отменено."
PRIVACY_DELETE_EXPIRED = (
    "⌛ Время подтверждения истекло, данные не удалены. " "Вызовите `!delete_user_data` заново."
)
VOICE_SELECT_EXPIRED = "⌛ Время выбора канала истекло. Нажмите «Вызвать на обзвон» заново."
PRIVACY_DELETE_FAILED = (
    "⚠️ Удаление данных завершилось ошибкой. Часть данных могла остаться — "
    "подробности в логе бота, повторите команду после устранения причины."
)
PRIVACY_DELETE_NOT_ADMIN = "Это подтверждение доступно только администратору, вызвавшему команду."
PRIVACY_DELETE_DONE = (
    "✅ Данные пользователя удалены в объёме политики приватности: "
    "тикеты анонимизированы — {tickets} (каналов закрыто — {channels}), "
    "сообщений лог-центра удалено — {log_messages}, "
    "AFK-записи — {afk_users}, AFK-статистика — {afk_stats}, кулдауны — {afk_cooldown}. "
    "Копии в резервных бэкапах исчезают по мере ротации бэкапов (см. docs/privacy.md)."
)
PRIVACY_AUDIT_TITLE = "🧾 Удаление персональных данных"
RETENTION_AUDIT_TITLE = "🧾 Ретенция: очистка старых заявок"

# Срок хранения тикетов и транскриптов (дни), по умолчанию 180.
# Записи старше срока удаляются фоновой задачей вместе с привязанными
# сообщениями лог-центра (docs/privacy.md).
TICKET_RETENTION_DAYS = _bounded_int_env("TICKET_RETENTION_DAYS", 180, IntBounds(1, 3650, "дн"))
RETENTION_CHECK_SECONDS = _bounded_int_env(
    "RETENTION_CHECK_SECONDS", 86400, IntBounds(60, 604800, "сек")
)

# Тикет, застрявший в состоянии processing дольше этого срока, считается
# брошенным: reconciliation вернёт его в работу или закроет.
TICKET_PROCESSING_TIMEOUT_SECONDS = _bounded_int_env(
    "TICKET_PROCESSING_TIMEOUT_SECONDS", 300, IntBounds(30, 86400, "сек")
)
TICKET_RECONCILE_CHECK_SECONDS = _bounded_int_env(
    "TICKET_RECONCILE_CHECK_SECONDS", 600, IntBounds(60, 86400, "сек")
)

# Поля эмбеда !afk_check
AFK_FIELD_STATUS = "Статус"
AFK_FIELD_REASON = "Причина"
AFK_FIELD_LEFT = "Ушёл"
AFK_FIELD_DURATION = "Время в AFK"


# ---------------------------------------------------------------------------
# Время
#
# Хранение и сравнение — всегда в UTC (utils/clock.py). GUILD_TIMEZONE
# задаёт пояс сообщества: в нём разбирается пользовательский ввод «23:45»
# и считается «день» дневной статистики. LEGACY_TIMEZONE — пояс, в котором
# исторические наивные даты записаны старыми версиями бота; используется
# один раз, миграцией схемы v4.
# ---------------------------------------------------------------------------

GUILD_TIMEZONE = _timezone_env("GUILD_TIMEZONE", "UTC")
LEGACY_TIMEZONE = _timezone_env("LEGACY_TIMEZONE", "UTC")

# ---------------------------------------------------------------------------
# Лимиты in-memory rate limiter
# ---------------------------------------------------------------------------

RATELIMIT_MAX_ENTRIES = 10000  # жёсткий потолок ключей в памяти
RATELIMIT_CLEANUP_INTERVAL_SECONDS = 60

# Лимиты Discord, на которые опирается валидация
DISCORD_MODAL_MAX_FIELDS = 5
DISCORD_LABEL_MAX_LENGTH = 45
DISCORD_TEXT_INPUT_MAX_LENGTH = 4000
DISCORD_TEXT_INPUT_PLACEHOLDER_MAX_LENGTH = 100
DISCORD_EMBED_DESCRIPTION_MAX = 4096
DISCORD_EMBED_FIELD_NAME_MAX = 256
DISCORD_EMBED_FIELD_VALUE_MAX = 1024
DISCORD_CHANNEL_NAME_MAX = 100
AFK_LIST_DESCRIPTION_MAX = 3900
TICKET_CHANNEL_SLUG_MAX_LENGTH = 90
TICKET_MULTILINE_FIELD_MIN_LENGTH = 151
TICKET_DECISION_REASON_MAX_LENGTH = 500
MIN_RATELIMIT_ENTRIES = 100


def _validate_forms(errors: list[str]) -> None:
    for form in TICKET_FORMS:
        if len(form.fields) > DISCORD_MODAL_MAX_FIELDS:
            errors.append(
                f"{form.ticket_type}: полей {len(form.fields)}, а модалка вмещает максимум "
                f"{DISCORD_MODAL_MAX_FIELDS}"
            )
        if not form.fields:
            errors.append(f"{form.ticket_type}: форма без полей не имеет смысла")
        for field in form.fields:
            if len(field.label) > DISCORD_LABEL_MAX_LENGTH:
                errors.append(
                    f"{form.ticket_type}: label «{field.label[:30]}…» длиной {len(field.label)} > "
                    f"{DISCORD_LABEL_MAX_LENGTH} символов"
                )
            if not 1 <= field.max_length <= DISCORD_TEXT_INPUT_MAX_LENGTH:
                errors.append(
                    f"{form.ticket_type}: max_length поля «{field.label[:30]}» вне диапазона "
                    f"1–{DISCORD_TEXT_INPUT_MAX_LENGTH}"
                )
            if len(field.placeholder) > DISCORD_TEXT_INPUT_PLACEHOLDER_MAX_LENGTH:
                errors.append(
                    f"{form.ticket_type}: placeholder поля «{field.label[:30]}» длиннее "
                    f"{DISCORD_TEXT_INPUT_PLACEHOLDER_MAX_LENGTH} символов"
                )

    if any(not form.title for form in TICKET_FORMS):
        errors.append("Заголовки форм заявок не должны быть пустыми")

    if len(FAMQCORE_EMBED_DESCRIPTION) > DISCORD_EMBED_DESCRIPTION_MAX:
        errors.append(
            f"FAMQCORE_EMBED_DESCRIPTION длиннее лимита Discord "
            f"({DISCORD_EMBED_DESCRIPTION_MAX} символов)"
        )


def _validate_ids(errors: list[str]) -> None:
    """Уникальность ID и согласованность списка голосовых каналов."""
    named_ids = {
        "TICKETS_CATEGORY_ID": TICKETS_CATEGORY_ID,
        "ROLE_APPLIED_ID": ROLE_APPLIED_ID,
        "ROLE_RECRUITER_ID": ROLE_RECRUITER_ID,
        "ROLE_OWNER_ID": ROLE_OWNER_ID,
        "ROLE_DEP_OWNER_ID": ROLE_DEP_OWNER_ID,
        "ROLE_ADMIN_ID": ROLE_ADMIN_ID,
        "ROLE_SUPPORT_ID": ROLE_SUPPORT_ID,
        "LOG_CHANNEL_ID": LOG_CHANNEL_ID,
    }
    for key, value in LOG_THREAD_IDS.items():
        named_ids[LOG_THREAD_ENV_NAMES.get(key, key)] = value

    seen: dict[int, str] = {}
    for name, value in named_ids.items():
        if value is None:
            continue
        previous = seen.get(value)
        if previous is not None:
            errors.append(f"{name} и {previous} указывают на один и тот же ID {value}")
        else:
            seen[value] = name

    for index, channel_id in enumerate(VOICE_CHANNEL_IDS):
        previous = seen.get(channel_id)
        if previous is not None:
            errors.append(
                f"VOICE_CHANNEL_IDS[{index}] и {previous} указывают на один и тот же ID "
                f"{channel_id}"
            )
        else:
            seen[channel_id] = f"VOICE_CHANNEL_IDS[{index}]"

    if VOICE_CHANNEL_IDS and len(VOICE_CHANNEL_IDS) != len(VOICE_CHANNELS):
        errors.append(
            f"VOICE_CHANNEL_IDS: задано {len(VOICE_CHANNEL_IDS)} ID, "
            f"а кнопок обзвона {len(VOICE_CHANNELS)} — "
            "кнопки без ID не смогут найти канал в production-режиме"
        )

    # ветка логов без своего канала не однозначна: родительская
    # приватность проверяется у канала, поэтому он обязателен
    if LOG_CHANNEL_ID is None and any(LOG_THREAD_IDS.values()):
        errors.append(
            "Заданы ID веток лог-центра без LOG_CHANNEL_ID — "
            "задайте и канал, иначе приватность ветки нечем гарантировать"
        )


def _validate_intervals(errors: list[str]) -> None:
    if AFK_MIN_DURATION_MINUTES < 1:
        errors.append("AFK_MIN_DURATION_MINUTES должен быть положительным")
    if AFK_MAX_DURATION_MINUTES <= AFK_MIN_DURATION_MINUTES:
        errors.append("AFK_MAX_DURATION_MINUTES должен быть больше AFK_MIN_DURATION_MINUTES")
    if AFK_COOLDOWN_SECONDS < 1:
        errors.append("AFK_COOLDOWN_SECONDS должен быть положительным")
    if VOICE_CALL_BUTTON_COOLDOWN_SECONDS < 1:
        errors.append("VOICE_CALL_BUTTON_COOLDOWN_SECONDS должен быть положительным")
    if AFK_MAX_MENTIONS_PER_MESSAGE < 1:
        errors.append("AFK_MAX_MENTIONS_PER_MESSAGE должен быть положительным")
    if TICKET_PROCESSING_TIMEOUT_SECONDS >= TICKET_RETENTION_DAYS * 86400:
        errors.append("TICKET_PROCESSING_TIMEOUT_SECONDS не может превышать срок хранения заявок")
    if RATELIMIT_MAX_ENTRIES < MIN_RATELIMIT_ENTRIES:
        errors.append(f"RATELIMIT_MAX_ENTRIES слишком мал (< {MIN_RATELIMIT_ENTRIES})")


def _validate_production_safety(errors: list[str]) -> None:
    """Небезопасные для production режимы и отсутствие обязательных секретов."""
    if ALLOW_NAME_FALLBACK and ENVIRONMENT == "production":
        errors.append(
            "ALLOW_NAME_FALLBACK=true недопустим при ENVIRONMENT=production: "
            "поиск объектов по имени открывает доступ к тикетам и логам "
            "одноимённым ролям"
        )
    if ENVIRONMENT not in ("production", "development"):
        errors.append(
            f"ENVIRONMENT: «{ENVIRONMENT}» — допустимы значения production или development"
        )


def validate(*, require_token: bool = False) -> list[str]:
    """Полный отчёт о проблемах конфигурации (пустой список = всё ок).

    Собирает все ошибки сразу, чтобы оператор исправил их за один заход,
    а не перезапускал бота на каждой. Значение TOKEN в отчёт не попадает.
    """
    errors = list(_ENV_ERRORS)
    _validate_forms(errors)
    _validate_ids(errors)
    _validate_intervals(errors)
    _validate_production_safety(errors)

    if require_token and not TOKEN:
        errors.append("TOKEN не задан. Заполните .env по образцу .env.example")

    return errors
