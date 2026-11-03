"""Configuration source; the bug is in the middleware default propagation."""

DEFAULT_TIMEOUT_SECONDS = 30


def load_settings(environment):
    return {"timeout_seconds": int(environment.get("API_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))}

