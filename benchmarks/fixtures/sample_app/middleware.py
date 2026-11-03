"""Request timeout middleware."""

from .settings import DEFAULT_TIMEOUT_SECONDS


def build_timeout_policy(settings, request_timeout=None):
    if request_timeout is not None:
        timeout = request_timeout
    else:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return {"timeout": timeout, "source": "request" if request_timeout is not None else "settings"}

