"""
monitoring/logging_config.py — Structured Logging for CloudWatch

Sets up structlog with JSON output for production (CloudWatch compatible)
and pretty console output for local development.

Usage in asgi.py:
    from monitoring.logging_config import setup_logging
    setup_logging()

Then anywhere:
    import structlog
    logger = structlog.get_logger()
    logger.info("test_run_started", spec_id="abc", workflow="api_test")
"""
from __future__ import annotations

import os
import sys
import logging

import structlog


def setup_logging(env: str = ""):
    """
    Configure structured logging based on environment.
    - production/staging: JSON lines (CloudWatch compatible)
    - development/test: Pretty colored console output
    """
    env = env or os.getenv("APP_ENV", "development")
    is_prod = env in ("production", "staging")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_prod:
        # JSON output for CloudWatch
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Pretty console for development
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # Also configure stdlib logging to go through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.INFO),
    )


def get_logger(name: str = ""):
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# ─── Pre-built loggers for common components ───

def agent_logger():
    return get_logger("agent")

def api_logger():
    return get_logger("api")

def auth_logger():
    return get_logger("auth")
