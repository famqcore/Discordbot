"""Единый источник времени.

Проверяется: хранение в UTC, лексикографическая сортируемость формата БД,
разбор пользовательского времени в поясе сообщества, переход на летнее
время и устойчивость к запуску процесса в другом часовом поясе.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import config
from afk.duration import parse_duration
from utils import clock


class FormatTestCase(unittest.TestCase):
    def test_to_db_is_utc_with_fixed_width(self):
        value = datetime(2024, 3, 1, 12, 30, 45, 123456, tzinfo=UTC)

        self.assertEqual(clock.to_db(value), "2024-03-01T12:30:45.123456+00:00")

    def test_all_timestamps_have_identical_length(self):
        moments = [
            datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
            clock.utcnow(),
        ]

        lengths = {len(clock.to_db(moment)) for moment in moments}
        self.assertEqual(len(lengths), 1)

    def test_lexicographic_order_matches_chronology(self):
        """Строки сравниваются в SQL напрямую — порядок обязан совпадать."""
        earlier = datetime(2024, 1, 9, 23, 59, 59, tzinfo=UTC)
        later = datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC)

        self.assertLess(clock.to_db(earlier), clock.to_db(later))

    def test_aware_non_utc_input_converted(self):
        moscow = datetime(2024, 1, 1, 15, 0, tzinfo=ZoneInfo("Europe/Moscow"))

        self.assertEqual(clock.to_db(moscow), "2024-01-01T12:00:00.000000+00:00")

    def test_utcnow_is_aware(self):
        self.assertIsNotNone(clock.utcnow().tzinfo)


class ParseTestCase(unittest.TestCase):
    def test_roundtrip(self):
        value = datetime(2024, 5, 5, 8, 9, 10, 111111, tzinfo=UTC)

        self.assertEqual(clock.parse_db(clock.to_db(value)), value)

    def test_parse_none_and_empty(self):
        self.assertIsNone(clock.parse_db(None))
        self.assertIsNone(clock.parse_db(""))
        self.assertIsNone(clock.parse_db("   "))

    def test_parse_broken_value(self):
        self.assertIsNone(clock.parse_db("не дата"))

    def test_parse_datetime_passthrough(self):
        value = datetime(2024, 1, 1, tzinfo=UTC)

        self.assertEqual(clock.parse_db(value), value)

    def test_naive_legacy_value_uses_legacy_timezone(self):
        """Старые наивные записи читаются в документированном поясе."""
        with patch.object(config, "LEGACY_TIMEZONE", "Europe/Moscow"):
            parsed = clock.parse_db("2024-01-01T15:00:00")

        self.assertEqual(parsed, datetime(2024, 1, 1, 12, 0, tzinfo=UTC))

    def test_naive_legacy_value_defaults_to_utc(self):
        with patch.object(config, "LEGACY_TIMEZONE", "UTC"):
            parsed = clock.parse_db("2024-01-01T15:00:00")

        self.assertEqual(parsed, datetime(2024, 1, 1, 15, 0, tzinfo=UTC))


class TimezoneTestCase(unittest.TestCase):
    def test_to_local_uses_guild_timezone(self):
        value = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Moscow"):
            self.assertEqual(clock.to_local(value).hour, 15)

    def test_local_date_uses_guild_timezone(self):
        """Дневная агрегация считается по локальному дню сообщества."""
        late_utc = datetime(2024, 1, 1, 23, 30, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Moscow"):
            self.assertEqual(clock.local_date(late_utc), "2024-01-02")
        with patch.object(config, "GUILD_TIMEZONE", "UTC"):
            self.assertEqual(clock.local_date(late_utc), "2024-01-01")

    def test_unknown_timezone_falls_back_to_utc(self):
        self.assertEqual(str(clock.timezone_by_name("Нет/Такого")), "UTC")

    def test_is_valid_timezone(self):
        self.assertTrue(clock.is_valid_timezone("Europe/Moscow"))
        self.assertFalse(clock.is_valid_timezone("Нет/Такого"))
        self.assertFalse(clock.is_valid_timezone(""))

    def test_process_timezone_does_not_affect_storage(self):
        """Рестарт в другом TZ не должен менять сохраняемое значение."""
        import os
        import time

        value = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
        expected = clock.to_db(value)
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Tokyo"
            time.tzset()
            self.assertEqual(clock.to_db(value), expected)
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()


class DstTestCase(unittest.TestCase):
    """Переходы на летнее/зимнее время."""

    def test_spring_forward_gap(self):
        # 31 марта 2024, 01:00 UTC = 03:00 в Берлине (час пропущен)
        moment = datetime(2024, 3, 31, 1, 0, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Berlin"):
            self.assertEqual(clock.to_local(moment).hour, 3)

    def test_autumn_fallback_offset(self):
        before = datetime(2024, 10, 27, 0, 0, tzinfo=UTC)
        after = datetime(2024, 10, 27, 2, 0, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Berlin"):
            self.assertEqual(clock.to_local(before).utcoffset(), timedelta(hours=2))
            self.assertEqual(clock.to_local(after).utcoffset(), timedelta(hours=1))

    def test_duration_across_dst_boundary_stays_wall_clock(self):
        """«Вернусь в 04:00» в ночь перевода часов остаётся будущим моментом."""
        now = datetime(2024, 3, 30, 23, 0, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Berlin"):
            parsed = parse_duration("04:00", now)

            self.assertGreater(parsed.return_at, now)
            local_return = clock.to_local(parsed.return_at)

        self.assertEqual(local_return.hour, 4)
        self.assertEqual(local_return.minute, 0)

    def test_named_time_keeps_exact_minute(self):
        """«Вернусь в 04:00» не должно превращаться в 03:59:xx."""
        now = datetime(2024, 6, 1, 23, 0, 30, tzinfo=UTC)

        with patch.object(config, "GUILD_TIMEZONE", "Europe/Berlin"):
            parsed = parse_duration("04:00", now)
            local_return = clock.to_local(parsed.return_at)

        self.assertEqual((local_return.hour, local_return.minute), (4, 0))
        self.assertEqual(local_return.second, 0)


class ArithmeticTestCase(unittest.TestCase):
    def test_seconds_between(self):
        start = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 10, 1, 30, tzinfo=UTC)

        self.assertEqual(clock.seconds_between(start, end), 90)

    def test_seconds_between_never_negative(self):
        start = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)

        self.assertEqual(clock.seconds_between(start, end), 0)

    def test_seconds_between_none_start(self):
        self.assertEqual(clock.seconds_between(None), 0)

    def test_shift(self):
        value = datetime(2024, 1, 10, tzinfo=UTC)

        self.assertEqual(clock.shift(value, days=-3), datetime(2024, 1, 7, tzinfo=UTC))

    def test_timestamp(self):
        value = datetime(2024, 1, 1, tzinfo=UTC)

        self.assertEqual(clock.timestamp(value), int(value.timestamp()))

    def test_parse_date(self):
        self.assertEqual(clock.parse_date("2024-02-29").day, 29)
        self.assertIsNone(clock.parse_date("29.02.2024"))
        self.assertIsNone(clock.parse_date(""))


if __name__ == "__main__":
    unittest.main()
