import logging
import os
import sys
from pathlib import Path

from core.utils.config import output_files_dir


# Logs follow the same OUTPUT_DIR redirection as every other runtime artifact
# so that read-only skill install directories (e.g. codex sandboxed runtimes)
# don't break module import. Default → <output_files_dir>/logs.
LOG_DIR = Path(output_files_dir) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _silence_noisy_third_party_loggers() -> None:
    """抑制第三方库默认 INFO 级别的请求/响应噪音日志。"""
    for name in ("httpx", "httpcore", "openai", "urllib3", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


_silence_noisy_third_party_loggers()


def _build_stdlib_logger() -> logging.Logger:
    logger = logging.getLogger("slidea")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


try:
    from loguru import logger as loguru_logger
except ImportError:
    logger = _build_stdlib_logger()
else:
    LOG_FILE_PATH = os.path.join(output_files_dir, "logs", "app_{time:YYYY-MM-DD}.log")
    my_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    loguru_logger.remove()
    loguru_logger.add(
        sys.stdout,
        format=my_format,
        level="INFO",
        colorize=True,
        enqueue=True,
    )
    loguru_logger.add(
        LOG_FILE_PATH,
        format=my_format,
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        colorize=True,
    )
    logger = loguru_logger


__all__ = ["logger"]
