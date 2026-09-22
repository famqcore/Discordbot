"""In-memory rate limiter с ограниченной памятью (issue #15).

Свойства:

- истёкшие записи удаляются при обращении и периодической уборкой, поэтому
  память не растёт вместе с аптаймом;
- есть жёсткий потолок ``max_entries`` и предсказуемая политика вытеснения:
  сначала истёкшие, затем самые ранние по времени окончания окна (LRU по
  сроку жизни);
- время берётся из ``monotonic`` — перевод системных часов не ломает окна;
- состояние живёт в экземпляре ``RateLimiter``, а не в неявной глобальной
  переменной: у теста, у каждого bot instance может быть свой лимитер.
  Модульные функции работают с одним общим экземпляром по умолчанию;
- ``stats()`` отдаёт счётчики (размер, отказы, вытеснения) без ключей и
  пользовательских данных.
"""

from __future__ import annotations

import threading
from time import monotonic

import config


class RateLimiter:
    def __init__(
        self,
        max_entries: int | None = None,
        cleanup_interval: float | None = None,
        time_source=monotonic,
    ) -> None:
        self._buckets: dict[tuple, float] = {}
        self._counts: dict[tuple, int] = {}
        self._lock = threading.Lock()
        self._time = time_source
        self._max_entries = max_entries or config.RATELIMIT_MAX_ENTRIES
        self._cleanup_interval = (
            cleanup_interval
            if cleanup_interval is not None
            else config.RATELIMIT_CLEANUP_INTERVAL_SECONDS
        )
        self._last_cleanup = self._time()
        self.allowed = 0
        self.rejected = 0
        self.evicted = 0
        self.expired = 0

    # -- обслуживание -------------------------------------------------------

    def _drop_expired(self, now: float) -> int:
        stale = [key for key, until in self._buckets.items() if until <= now]
        for key in stale:
            del self._buckets[key]
            self._counts.pop(key, None)
        self.expired += len(stale)
        self._last_cleanup = now
        return len(stale)

    def _enforce_capacity(self, now: float) -> None:
        if len(self._buckets) < self._max_entries:
            return
        self._drop_expired(now)
        if len(self._buckets) < self._max_entries:
            return
        # потолок достигнут живыми окнами: вытесняем те, что закончатся раньше
        overflow = len(self._buckets) - self._max_entries + 1
        oldest = sorted(self._buckets.items(), key=lambda item: item[1])[:overflow]
        for key, _ in oldest:
            del self._buckets[key]
            self._counts.pop(key, None)
        self.evicted += len(oldest)

    def _maybe_cleanup(self, now: float) -> None:
        if now - self._last_cleanup >= self._cleanup_interval:
            self._drop_expired(now)

    # -- публичный интерфейс ------------------------------------------------

    def retry_after(self, key: tuple, seconds: float) -> int:
        """Сколько секунд ждать; 0 означает, что действие разрешено."""
        now = self._time()
        with self._lock:
            self._maybe_cleanup(now)
            until = self._buckets.get(key)
            if until is not None:
                if until > now:
                    self.rejected += 1
                    return max(1, int(until - now))
                del self._buckets[key]
                self.expired += 1
            self._enforce_capacity(now)
            self._buckets[key] = now + seconds
            self.allowed += 1
            return 0

    def hits(self, key: tuple, limit: int, window_seconds: float) -> bool:
        """Скользящее окно «limit срабатываний за window_seconds».

        True — действие разрешено. В отличие от ``retry_after`` допускает
        несколько срабатываний внутри окна (лимит автоответов на канал).
        """
        now = self._time()
        with self._lock:
            self._maybe_cleanup(now)
            window_end = self._buckets.get(key)
            if window_end is None or window_end <= now:
                self._enforce_capacity(now)
                self._buckets[key] = now + window_seconds
                self._counts[key] = 1
                self.allowed += 1
                return True
            count = self._counts.get(key, 0)
            if count >= limit:
                self.rejected += 1
                return False
            self._counts[key] = count + 1
            self.allowed += 1
            return True

    def release(self, key: tuple) -> None:
        """Снимает резерв (действие не состоялось — окно занимать незачем)."""
        with self._lock:
            self._buckets.pop(key, None)
            self._counts.pop(key, None)

    def cleanup(self) -> int:
        """Принудительная уборка истёкших записей. Возвращает число удалённых."""
        now = self._time()
        with self._lock:
            removed = self._drop_expired(now)
            for key in [k for k in self._counts if k not in self._buckets]:
                del self._counts[key]
            return removed

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._buckets),
                "capacity": self._max_entries,
                "allowed": self.allowed,
                "rejected": self.rejected,
                "evicted": self.evicted,
                "expired": self.expired,
            }

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._counts.clear()
            self.allowed = 0
            self.rejected = 0
            self.evicted = 0
            self.expired = 0
            self._last_cleanup = self._time()


_default = RateLimiter()


def limiter() -> RateLimiter:
    return _default


def retry_after(key: tuple, seconds: float) -> int:
    return _default.retry_after(key, seconds)


def allow_within_window(key: tuple, limit: int, window_seconds: float) -> bool:
    return _default.hits(key, limit, window_seconds)


def release(key: tuple) -> None:
    _default.release(key)


def cleanup() -> int:
    return _default.cleanup()


def stats() -> dict[str, int]:
    return _default.stats()


def reset() -> None:
    _default.reset()
