import re
from typing import Any

_SECRET_KEY_PARTS = {
    "accesstoken",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "refreshtoken",
    "secret",
    "token",
}


def redact(value: Any) -> Any:
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if is_secret_key(str(key)):
            result[key] = "[REDACTED]"
        else:
            result[key] = redact(item)
    return result


def is_secret_key(key: str) -> bool:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    parts = {part for part in re.split(r"[^a-z0-9]+", words.lower()) if part}
    normalized_key = "".join(parts)
    return (
        "token" in parts
        or "cookie" in parts
        or normalized_key in _SECRET_KEY_PARTS
        or any(
            normalized_key.endswith(part)
            for part in _SECRET_KEY_PARTS
            if part not in {"token", "cookie"}
        )
    )
