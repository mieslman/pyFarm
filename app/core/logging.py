import sys
from typing import Any

from loguru import logger

SENSITIVE_KEYS = {"password", "pass", "token", "secret", "session_token"}


def mask_sensitive_data(data: Any) -> Any:
    """Mask sensitive fields such as passwords or tokens before logging."""
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
                masked[key] = "***"
            elif isinstance(value, (dict, list)):
                masked[key] = mask_sensitive_data(value)
            else:
                masked[key] = value
        return masked
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    return data


def setup_logging(debug: bool = False):
    """Configure Loguru structured logging."""
    logger.remove()
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    log_level = "DEBUG" if debug else "INFO"
    logger.add(sys.stdout, format=log_format, level=log_level, colorize=True)
    logger.add("logs/myfreefarm.log", rotation="10 MB", retention="14 days", level="DEBUG")
    return logger
