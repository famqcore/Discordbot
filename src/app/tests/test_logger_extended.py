import logging
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from utils.logger import ColoredFormatter, setup_logger


class TestColoredFormatter(unittest.TestCase):
    def test_format_includes_levelname(self):
        formatter = ColoredFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        self.assertIn("INFO", result)
        self.assertIn("test message", result)

    def test_format_debug_color(self):
        formatter = ColoredFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="debug msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        self.assertIn("DEBUG", result)

    def test_format_warning_color(self):
        formatter = ColoredFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="warning msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        self.assertIn("WARNING", result)

    def test_format_error_color(self):
        formatter = ColoredFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="error msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        self.assertIn("ERROR", result)


class TestSetupLoggerExtended(unittest.TestCase):
    def _cleanup_logger(self, logger):
        for h in logger.handlers:
            h.close()
        logger.handlers.clear()

    def test_logger_has_file_handler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "LOG_DIR", tmpdir):
                logger = setup_logger("test_file_handler")
                has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
                self.assertTrue(has_file)
                self._cleanup_logger(logger)

    def test_logger_has_stream_handler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "LOG_DIR", tmpdir):
                logger = setup_logger("test_stream_handler")
                has_stream = any(
                    isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                    for h in logger.handlers
                )
                self.assertTrue(has_stream)
                self._cleanup_logger(logger)

    def test_log_file_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "LOG_DIR", tmpdir):
                logger = setup_logger("test_log_creation")
                logger.info("test message")
                log_files = [f for f in os.listdir(tmpdir) if f.endswith(".log")]
                self.assertTrue(len(log_files) > 0)
                self._cleanup_logger(logger)

    def test_multiple_loggers_different_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "LOG_DIR", tmpdir):
                logger1 = setup_logger("logger1")
                logger2 = setup_logger("logger2")
                self.assertNotEqual(logger1.name, logger2.name)
                self._cleanup_logger(logger1)
                self._cleanup_logger(logger2)


if __name__ == "__main__":
    unittest.main()
