"""Единый источник времени.

Всё приложение хранит и сравнивает время в UTC. Правила:

- в БД пишется строка фиксированной ширины `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`
  (``to_db``), поэтому лексикографическое сравнение в SQL совпадает
  с хронологическим;
- в коде используются только timezone-aware `datetime` в UTC (``utcnow``);
- пользовательское время (например «23:45») разбирается в часовом поясе
  сообщества (``guild_timezone``) и конвертируется в UTC;
- исторические наивные значения читаются как время в ``LEGACY_TIMEZONE``
  (по умолчанию UTC) — см. миграцию схемы v4.

Точка входа одна, поэтому в тестах достаточно подменить ``utcnow``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DB_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"
DATE_FORMAT = "%Y-%m-%d"


def utcnow() -> datetime:
    """Текущее время в UTC (timezone-aware)."""
    return datetime.now(UTC)


def guild_timezone() -> ZoneInfo:
    """Часовой пояс сообщества из конфигурации (по умолчанию UTC)."""
    import config

    return timezone_by_name(getattr(config, "GUILD_TIMEZONE", "UTC"))


def legacy_timezone() -> ZoneInfo:
    """Пояс, в котором записаны наивные значения старых версий бота."""
    import config

    return timezone_by_name(getattr(config, "LEGACY_TIMEZONE", "UTC"))


def timezone_by_name(name: str) -> ZoneInfo:
    """ZoneInfo по имени; неизвестное имя даёт UTC (валидация — в config)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


def to_utc(value: datetime) -> datetime:
    """Приводит datetime к UTC; наивное значение считается legacy-временем."""
    if value.tzinfo is None:
        return value.replace(tzinfo=legacy_timezone()).astimezone(UTC)
    return value.astimezone(UTC)


def to_db(value: datetime | None = None) -> str:
    """Строка для хранения в SQLite (UTC, фиксированная ширина)."""
    moment = utcnow() if value is None else to_utc(value)
    return moment.strftime(DB_FORMAT)


def parse_db(value: str | datetime | None) -> datetime | None:
    """Читает значение из БД. None — если строка пустая или битая."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return to_utc(parsed)


def to_local(value: datetime) -> datetime:
    """Время в часовом поясе сообщества — только для показа пользователю."""
    return to_utc(value).astimezone(guild_timezone())


def local_date(value: datetime | None = None) -> str:
    """День YYYY-MM-DD в поясе сообщества (ключ дневной статистики)."""
    moment = utcnow() if value is None else value
    return to_local(moment).strftime(DATE_FORMAT)


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, DATE_FORMAT).date()
    except (ValueError, TypeError):
        return None


def seconds_between(start: datetime | None, end: datetime | None = None) -> int:
    """Секунды между двумя моментами, никогда не отрицательные."""
    if start is None:
        return 0
    finish = utcnow() if end is None else end
    delta = to_utc(finish) - to_utc(start)
    return max(int(delta.total_seconds()), 0)


def shift(value: datetime, **kwargs) -> datetime:
    return to_utc(value) + timedelta(**kwargs)


def timestamp(value: datetime) -> int:
    """Unix-время для Discord-таймстампов (<t:...>)."""
    return int(to_utc(value).timestamp())
