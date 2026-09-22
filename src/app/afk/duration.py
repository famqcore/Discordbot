"""Разбор длительности AFK (issue #13).

Поддерживаемая грамматика (полное совпадение, регистр не важен,
необязательный префикс «через»):

```
duration := [ "через" ] ( clock | span { span } )
clock    := HH ":" MM                 # ближайшее наступление этого времени
span     := INT unit
unit     := "ч" | "час" | "часа" | "часов" | "h"
          | "м" | "мин" | "минут" | "минута" | "минуты" | "m"
          | "д" | "дн" | "день" | "дня" | "дней" | "d"
```

Примеры: `2 часа`, `30 мин`, `1 ч 30 мин`, `2д`, `23:45`, `через 15м`.

Ограничения: длительность от ``AFK_MIN_DURATION_MINUTES`` до
``AFK_MAX_DURATION_MINUTES``. Частичное совпадение («2 часа что угодно»)
отклоняется, переполнение невозможно — число проверяется до ``timedelta``.
Нулевая длительность запрещена: для немедленного возврата есть кнопка
«Отменить AFK».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from utils import clock

_UNITS = {
    "ч": "hours",
    "час": "hours",
    "часа": "hours",
    "часов": "hours",
    "h": "hours",
    "м": "minutes",
    "мин": "minutes",
    "минут": "minutes",
    "минута": "minutes",
    "минуты": "minutes",
    "минуту": "minutes",
    "m": "minutes",
    "д": "days",
    "дн": "days",
    "день": "days",
    "дня": "days",
    "дней": "days",
    "d": "days",
}

_UNIT_PATTERN = "|".join(sorted(_UNITS, key=len, reverse=True))
_SPAN_RE = re.compile(rf"^(?:(\d{{1,6}})\s*({_UNIT_PATTERN})\s*)+$", re.IGNORECASE)
_SPAN_ITEM_RE = re.compile(rf"(\d{{1,6}})\s*({_UNIT_PATTERN})", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_PREFIX_RE = re.compile(r"^через\s+", re.IGNORECASE)

MINUTES_PER_UNIT = {"minutes": 1, "hours": 60, "days": 24 * 60}


class DurationError(ValueError):
    """Ввод не соответствует грамматике или выходит за границы."""


@dataclass(frozen=True)
class ParsedDuration:
    """Результат разбора: момент возврата в UTC и его длительность."""

    return_at: datetime
    minutes: int


def _limits_text() -> str:
    return (
        f"от {format_minutes(config.AFK_MIN_DURATION_MINUTES)} "
        f"до {format_minutes(config.AFK_MAX_DURATION_MINUTES)}"
    )


def format_minutes(total_minutes: int) -> str:
    days, rest = divmod(max(int(total_minutes), 0), 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def parse_duration(text: str, now: datetime | None = None) -> ParsedDuration:
    """Разбирает пользовательский ввод. Бросает ``DurationError`` с причиной."""
    if text is None:
        raise DurationError("Укажите, на сколько вы уходите. Например: «2 часа» или «23:45».")

    cleaned = _PREFIX_RE.sub("", text.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise DurationError("Укажите, на сколько вы уходите. Например: «2 часа» или «23:45».")

    moment = now or clock.utcnow()

    clock_match = _CLOCK_RE.match(cleaned)
    if clock_match:
        return _parse_clock(clock_match, moment)

    if not _SPAN_RE.match(cleaned):
        raise DurationError(
            "Не понимаю формат времени. Примеры: «2 часа», «30 мин», «1 ч 30 мин», «23:45»."
        )

    total_minutes = 0
    seen_units: set[str] = set()
    for value, unit in _SPAN_ITEM_RE.findall(cleaned):
        kind = _UNITS[unit.lower()]
        if kind in seen_units:
            raise DurationError(
                "Единица времени указана дважды. Напишите одно значение: например «1 ч 30 мин»."
            )
        seen_units.add(kind)
        total_minutes += int(value) * MINUTES_PER_UNIT[kind]

    return ParsedDuration(
        return_at=_validated_return(moment, total_minutes),
        minutes=total_minutes,
    )


def _parse_clock(match: re.Match, moment: datetime) -> ParsedDuration:
    """«23:45» — ближайшее наступление этого времени в поясе сообщества."""
    hour, minute = int(match.group(1)), int(match.group(2))
    local_now = clock.to_local(moment)
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        # через полночь: то же время следующего дня. Пересчёт идёт через
        # локальную дату, поэтому переход на летнее время не сдвигает результат
        target = (local_now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

    return_at = clock.to_utc(target)
    minutes = int((return_at - moment).total_seconds() // 60)
    # Границы проверяем по длительности, но возвращаем именно названный
    # момент: пересчёт «момент + minutes» терял бы секунды и превращал
    # «вернусь в 04:00» в 03:59:xx.
    _validated_return(moment, minutes)
    return ParsedDuration(return_at=return_at, minutes=minutes)


def _validated_return(moment: datetime, minutes: int) -> datetime:
    if minutes < config.AFK_MIN_DURATION_MINUTES:
        raise DurationError(
            f"Слишком короткий AFK. Допустимо {_limits_text()}. "
            "Если возвращаться не нужно — не берите AFK."
        )
    if minutes > config.AFK_MAX_DURATION_MINUTES:
        raise DurationError(f"Слишком долгий AFK. Допустимо {_limits_text()}.")
    try:
        return clock.to_utc(moment) + timedelta(minutes=minutes)
    except OverflowError as error:  # pragma: no cover - отсекается проверкой выше
        raise DurationError(f"Слишком долгий AFK. Допустимо {_limits_text()}.") from error


def parse_return_time(text: str, now: datetime | None = None) -> datetime | None:
    """Совместимый разбор: возвращает момент возврата либо None."""
    try:
        return parse_duration(text, now).return_at
    except DurationError:
        return None
