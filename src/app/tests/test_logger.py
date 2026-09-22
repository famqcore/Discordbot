import logging
import os
import shutil
import tempfile
import unittest

import config
from utils.logger import ColoredFormatter, setup_logger


class TestLogger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.old_log_dir = config.LOG_DIR
        config.LOG_DIR = self.temp_dir

    def tearDown(self):
        config.LOG_DIR = self.old_log_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_setup_logger_returns_logger(self):
        logger = setup_logger("test_logger")
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "test_logger")
        self.assertEqual(logger.level, logging.DEBUG)
        self.assertFalse(logger.propagate)

    def test_logger_has_handlers(self):
        logger = setup_logger("test_logger_handlers")
        self.assertEqual(len(logger.handlers), 2)
        handlers = [type(h).__name__ for h in logger.handlers]
        self.assertIn("RotatingFileHandler", handlers)
        self.assertIn("StreamHandler", handlers)

    def test_log_file_created(self):
        logger = setup_logger("test_log_file")
        logger.info("test message")
        log_path = os.path.join(self.temp_dir, "bot.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("test message", content)

    def test_colored_formatter_adds_ansi(self):
        formatter = ColoredFormatter("%(levelname)s | %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        self.assertIn("\033[32m", formatted)
        self.assertIn("INFO", formatted)
        self.assertIn("\033[0m", formatted)

    def test_log_levels(self):
        logger = setup_logger("test_levels")
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warning msg")
        logger.error("error msg")

        log_path = os.path.join(self.temp_dir, "bot.log")
        with open(log_path, encoding="utf-8") as f:
            content = f.read()

        self.assertIn("debug msg", content)
        self.assertIn("info msg", content)
        self.assertIn("warning msg", content)
        self.assertIn("error msg", content)


if __name__ == "__main__":
    unittest.main()
