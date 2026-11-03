from .middleware import build_timeout_policy
from .settings import load_settings


def prepare_request(environment, request_timeout=None):
    settings = load_settings(environment)
    return build_timeout_policy(settings, request_timeout)

