"""Ограниченный по памяти rate limiter.

Проверяется: срок жизни записей, жёсткий потолок и политика вытеснения,
отсутствие неявного общего состояния между экземплярами, счётчики,
потокобезопасность и независимость от перевода системных часов.
"""

import threading
import unittest

from utils.ratelimit import RateLimiter, limiter


class FakeClock:
    """Управляемый источник времени вместо monotonic."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RetryAfterTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(max_entries=100, time_source=self.clock)

    def test_first_call_allowed(self):
        self.assertEqual(self.limiter.retry_after(("k",), 10), 0)

    def test_second_call_within_window_blocked(self):
        self.limiter.retry_after(("k",), 10)

        self.assertGreater(self.limiter.retry_after(("k",), 10), 0)

    def test_call_after_window_allowed(self):
        self.limiter.retry_after(("k",), 10)
        self.clock.advance(11)

        self.assertEqual(self.limiter.retry_after(("k",), 10), 0)

    def test_remaining_seconds_reported(self):
        self.limiter.retry_after(("k",), 30)
        self.clock.advance(10)

        self.assertEqual(self.limiter.retry_after(("k",), 30), 20)

    def test_distinct_keys_are_independent(self):
        self.limiter.retry_after(("a",), 10)

        self.assertEqual(self.limiter.retry_after(("b",), 10), 0)

    def test_release_frees_the_window(self):
        self.limiter.retry_after(("k",), 10)
        self.limiter.release(("k",))

        self.assertEqual(self.limiter.retry_after(("k",), 10), 0)


class SlidingWindowTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(max_entries=100, time_source=self.clock)

    def test_allows_up_to_limit(self):
        results = [self.limiter.hits(("ch",), 3, 20) for _ in range(3)]

        self.assertEqual(results, [True, True, True])

    def test_blocks_beyond_limit(self):
        for _ in range(3):
            self.limiter.hits(("ch",), 3, 20)

        self.assertFalse(self.limiter.hits(("ch",), 3, 20))

    def test_window_resets_after_expiry(self):
        for _ in range(3):
            self.limiter.hits(("ch",), 3, 20)
        self.clock.advance(21)

        self.assertTrue(self.limiter.hits(("ch",), 3, 20))


class MemoryBoundsTestCase(unittest.TestCase):
    """Память не растёт вместе с аптаймом."""

    def test_expired_entries_removed_by_cleanup(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=1000, time_source=clock)
        for i in range(50):
            rl.retry_after((i,), 10)
        clock.advance(11)

        removed = rl.cleanup()

        self.assertEqual(removed, 50)
        self.assertEqual(rl.stats()["size"], 0)

    def test_hard_cap_is_never_exceeded(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=10, time_source=clock)

        for i in range(200):
            rl.retry_after((i,), 3600)

        self.assertLessEqual(rl.stats()["size"], 10)
        self.assertGreater(rl.stats()["evicted"], 0)

    def test_eviction_prefers_expired_over_live(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=5, time_source=clock)
        for i in range(5):
            rl.retry_after((f"short{i}",), 10)
        clock.advance(11)

        rl.retry_after(("fresh",), 600)

        self.assertEqual(rl.stats()["size"], 1)
        self.assertEqual(rl.stats()["evicted"], 0)

    def test_eviction_drops_soonest_expiring_first(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=3, time_source=clock)
        rl.retry_after(("short",), 10)
        rl.retry_after(("medium",), 100)
        rl.retry_after(("long",), 1000)

        rl.retry_after(("new",), 1000)

        # «short» истекает раньше всех — вытесняется первым
        self.assertEqual(rl.retry_after(("short",), 10), 0)
        self.assertGreater(rl.retry_after(("long",), 1000), 0)

    def test_periodic_cleanup_runs_on_access(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=1000, cleanup_interval=60, time_source=clock)
        for i in range(20):
            rl.retry_after((i,), 10)
        clock.advance(120)

        rl.retry_after(("trigger",), 10)

        self.assertEqual(rl.stats()["size"], 1)

    def test_counts_cleaned_with_buckets(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=100, time_source=clock)
        rl.hits(("ch",), 5, 10)
        clock.advance(11)

        rl.cleanup()

        self.assertEqual(rl.stats()["size"], 0)


class IsolationTestCase(unittest.TestCase):
    """Нет неявного общего состояния между экземплярами и тестами."""

    def test_instances_do_not_share_state(self):
        first = RateLimiter(max_entries=10)
        second = RateLimiter(max_entries=10)

        first.retry_after(("k",), 60)

        self.assertEqual(second.retry_after(("k",), 60), 0)

    def test_reset_clears_everything(self):
        rl = RateLimiter(max_entries=10)
        rl.retry_after(("k",), 60)

        rl.reset()

        self.assertEqual(rl.stats()["size"], 0)
        self.assertEqual(rl.stats()["allowed"], 0)
        self.assertEqual(rl.retry_after(("k",), 60), 0)

    def test_module_default_is_a_single_instance(self):
        self.assertIs(limiter(), limiter())


class StatsTestCase(unittest.TestCase):
    def test_counters_reflect_activity(self):
        clock = FakeClock()
        rl = RateLimiter(max_entries=10, time_source=clock)
        rl.retry_after(("k",), 60)
        rl.retry_after(("k",), 60)

        stats = rl.stats()

        self.assertEqual(stats["allowed"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["capacity"], 10)

    def test_stats_contain_no_keys_or_payload(self):
        rl = RateLimiter(max_entries=10)
        rl.retry_after(("guild", 1, "user", 2), 60)

        self.assertEqual(
            set(rl.stats()),
            {"size", "capacity", "allowed", "rejected", "evicted", "expired"},
        )


class ConcurrencyTestCase(unittest.TestCase):
    def test_only_one_thread_wins_the_window(self):
        rl = RateLimiter(max_entries=100)
        results = []
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            results.append(rl.retry_after(("shared",), 60))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(0), 1)

    def test_sliding_window_respects_limit_under_load(self):
        rl = RateLimiter(max_entries=100)
        results = []
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            results.append(rl.hits(("channel",), 5, 60))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(True), 5)


if __name__ == "__main__":
    unittest.main()
