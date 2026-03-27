"""RedactingFilter for Python logging.

Prevents trace content (secrets, PII) from leaking into opentraces'
own debug logs. Attach this filter to any log handler that might
emit user trace data.
"""

from __future__ import annotations

import logging

from .secrets import redact_text, scan_text


class RedactingFilter(logging.Filter):
    """A logging filter that redacts secrets from log records.

    Scans the formatted log message for secret patterns and replaces
    matches with [REDACTED] before the record is emitted.

    Usage::

        import logging
        from opentraces.security.redactor import RedactingFilter

        handler = logging.StreamHandler()
        handler.addFilter(RedactingFilter())
        logger = logging.getLogger("opentraces")
        logger.addHandler(handler)
    """

    def __init__(self, name: str = "", include_entropy: bool = False):
        """Initialize the filter.

        Args:
            name: Logger name filter (standard logging.Filter behavior).
            include_entropy: Whether to flag high-entropy strings in logs.
                Defaults to False to avoid false positives in debug output.
        """
        super().__init__(name)
        self.include_entropy = include_entropy

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets in the log record message.

        Always returns True (the record is never suppressed, only sanitized).
        """
        if record.msg and isinstance(record.msg, str):
            matches = scan_text(record.msg, include_entropy=self.include_entropy)
            if matches:
                record.msg = redact_text(record.msg, matches)

        if record.args:
            if isinstance(record.args, dict):
                sanitized = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        m = scan_text(v, include_entropy=self.include_entropy)
                        sanitized[k] = redact_text(v, m) if m else v
                    else:
                        sanitized[k] = v
                record.args = sanitized
            elif isinstance(record.args, tuple):
                sanitized_list = []
                for v in record.args:
                    if isinstance(v, str):
                        m = scan_text(v, include_entropy=self.include_entropy)
                        sanitized_list.append(redact_text(v, m) if m else v)
                    else:
                        sanitized_list.append(v)
                record.args = tuple(sanitized_list)

        return True


def configure_logging(
    level: int = logging.WARNING,
    debug: bool = False,
) -> logging.Logger:
    """Configure opentraces logger with redacting filter.

    Args:
        level: Base logging level. Default WARNING.
        debug: If True, sets level to DEBUG and logs to stderr.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("opentraces")
    logger.setLevel(logging.DEBUG if debug else level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG if debug else level)
        handler.addFilter(RedactingFilter())
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
