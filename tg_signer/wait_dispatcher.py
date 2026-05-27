from __future__ import annotations


class BusinessRetryableError(RuntimeError):
    """Retryable task-level business failure raised by the event engine."""
