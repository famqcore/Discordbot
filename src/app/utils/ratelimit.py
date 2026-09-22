from time import monotonic

_buckets: dict[tuple, float] = {}


def retry_after(key: tuple, seconds: int) -> int:
    """Возвращает сколько секунд ждать; 0 означает, что действие разрешено."""
    now = monotonic()
    until = _buckets.get(key, 0)
    if until > now:
        return max(1, int(until - now))
    _buckets[key] = now + seconds
    return 0


def reset() -> None:
    _buckets.clear()
