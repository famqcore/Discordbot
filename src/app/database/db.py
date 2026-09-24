"""Доступ к SQLite: один выделенный поток, короткие явные транзакции.

Модель доступа:

- все запросы приложения выполняются в одном служебном потоке с одним
  долгоживущим соединением. Это убирает синхронный I/O из event loop
  (корутины ждут результат через ``arun``) и сериализует записи, поэтому
  конкурентные операции бота не порождают `database is locked`;
- PRAGMA (WAL, foreign_keys, busy_timeout, synchronous) применяются один раз
  при создании соединения, а не на каждый запрос;
- каждая операция — явная короткая транзакция: чтение в deferred, запись
  в ``BEGIN IMMEDIATE`` с commit/rollback;
- повтор выполняется только для ожидаемых busy/locked ошибок внешних
  процессов (бэкап, sqlite3 CLI) с ограниченным backoff;
- счётчики задержек и ошибок доступны через ``metrics()``.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import TypeVar

import config
from utils.logger import logger

T = TypeVar("T")

BUSY_TIMEOUT_MS = 5000
MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.05

_local = threading.local()


class Metrics:
    """Счётчики обращений к БД (без персональных данных)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.operations = 0
        self.writes = 0
        self.failures = 0
        self.conflicts = 0
        self.busy_retries = 0
        self.total_seconds = 0.0
        self.max_seconds = 0.0

    def record(self, *, write: bool, seconds: float) -> None:
        with self._lock:
            self.operations += 1
            if write:
                self.writes += 1
            self.total_seconds += seconds
            self.max_seconds = max(self.max_seconds, seconds)

    def record_retry(self) -> None:
        with self._lock:
            self.busy_retries += 1

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def record_conflict(self) -> None:
        """Нарушение ограничения целостности — ожидаемый исход гонки."""
        with self._lock:
            self.conflicts += 1

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            average = self.total_seconds / self.operations if self.operations else 0.0
            return {
                "operations": self.operations,
                "writes": self.writes,
                "failures": self.failures,
                "conflicts": self.conflicts,
                "busy_retries": self.busy_retries,
                "avg_seconds": round(average, 6),
                "max_seconds": round(self.max_seconds, 6),
            }

    def reset(self) -> None:
        with self._lock:
            self.operations = 0
            self.writes = 0
            self.failures = 0
            self.conflicts = 0
            self.busy_retries = 0
            self.total_seconds = 0.0
            self.max_seconds = 0.0


def connect(path: str | None = None) -> sqlite3.Connection:
    """Новое настроенное соединение.

    Используется служебным потоком, миграциями и обслуживающими скриптами.
    """
    target = path or config.DB_PATH
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        # in-memory и некоторые ФС не поддерживают WAL; остальные PRAGMA уже применены
        pass
    return conn


def _is_busy_error(error: sqlite3.Error) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


class Database:
    """Шлюз к SQLite с единственным рабочим потоком."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.metrics = Metrics()
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"sqlite-{os.path.basename(path)}"
        )
        self._closed = False

    # -- соединение ---------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        with self._conn_lock:
            if self._conn is None:
                self._conn = connect(self.path)
            return self._conn

    def _close_connection(self) -> None:
        """Закрывает соединение. Выполняется в потоке-владельце соединения."""
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def close(self) -> None:
        """Корректное завершение: дождаться очереди и закрыть соединение.

        sqlite3-соединение принадлежит рабочему потоку, поэтому закрывается
        задачей в нём же — иначе Python поднимет ProgrammingError.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._executor.submit(self._close_connection).result(timeout=10)
        except FuturesTimeoutError:
            logger.warning("database.close outcome=timeout timeout_seconds=10")
        except RuntimeError:
            # executor уже остановлен (интерпретатор завершается)
            pass
        finally:
            self._executor.shutdown(wait=True)

    # -- выполнение ---------------------------------------------------------

    def _execute(self, func: Callable[[sqlite3.Connection], T], write: bool) -> T:
        conn = self._connection()
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                else:
                    conn.execute("BEGIN")
                try:
                    result = func(conn)
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()
                self.metrics.record(write=write, seconds=time.monotonic() - started)
                return result
            except sqlite3.OperationalError as error:
                if not _is_busy_error(error) or attempt >= MAX_RETRIES:
                    self.metrics.record_failure()
                    raise
                attempt += 1
                self.metrics.record_retry()
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            except sqlite3.IntegrityError:
                # конфликт уникального индекса — штатный исход конкурентной
                # операции, вызывающий код обрабатывает его сам
                self.metrics.record_conflict()
                raise
            except sqlite3.Error:
                self.metrics.record_failure()
                raise

    def _in_worker(self) -> bool:
        return getattr(_local, "database_worker", None) is self

    def _worker_call(self, func: Callable[[sqlite3.Connection], T], write: bool) -> T:
        _local.database_worker = self
        try:
            return self._execute(func, write)
        finally:
            _local.database_worker = None

    def run(self, func: Callable[[sqlite3.Connection], T], *, write: bool = False) -> T:
        """Синхронно выполнить операцию в служебном потоке.

        Из корутин используйте ``arun``: ``run`` блокирует вызывающий поток.
        """
        if self._closed:
            raise RuntimeError("database gateway is closed")
        if self._in_worker():
            # вложенный вызов уже внутри транзакции служебного потока
            return func(self._connection())
        return self._executor.submit(self._worker_call, func, write).result()

    async def arun(self, func: Callable[[sqlite3.Connection], T], *, write: bool = False) -> T:
        """Асинхронно выполнить операцию, не блокируя event loop."""
        if self._closed:
            raise RuntimeError("database gateway is closed")
        if self._in_worker():
            return func(self._connection())
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._worker_call, func, write)


_gateway: Database | None = None
_gateway_lock = threading.Lock()


def gateway() -> Database:
    """Шлюз для текущего ``config.DB_PATH`` (пересоздаётся при смене пути)."""
    global _gateway
    with _gateway_lock:
        if _gateway is None or _gateway.path != config.DB_PATH or _gateway._closed:
            if _gateway is not None:
                _gateway.close()
            _gateway = Database(config.DB_PATH)
        return _gateway


def run(func: Callable[[sqlite3.Connection], T], *, write: bool = False) -> T:
    return gateway().run(func, write=write)


async def arun(func: Callable[[sqlite3.Connection], T], *, write: bool = False) -> T:
    return await gateway().arun(func, write=write)


def metrics() -> dict[str, float | int]:
    return gateway().metrics.snapshot()


def reset_metrics() -> None:
    gateway().metrics.reset()


def shutdown() -> None:
    """Закрывает соединение (вызывается при остановке бота и в тестах)."""
    global _gateway
    with _gateway_lock:
        if _gateway is not None:
            _gateway.close()
            _gateway = None


def get_db() -> sqlite3.Connection:
    """Отдельное соединение вне шлюза.

    Нужно миграциям, бэкапу и обслуживающим утилитам, которым требуется
    собственный контроль транзакции. Прикладной код ходит через ``run``.
    """
    return connect()


atexit.register(shutdown)

__all__ = [
    "Database",
    "arun",
    "connect",
    "gateway",
    "get_db",
    "metrics",
    "reset_metrics",
    "run",
    "shutdown",
]
