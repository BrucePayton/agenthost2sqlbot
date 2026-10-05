"""Expose validation locations without returning request values or credentials."""

from pydantic import ValidationError

from app.errors import AppError


def native_validation_error(exc: ValueError, stage: str) -> AppError:
    """Identify the rejected contract section while excluding input and context."""
    details = {"stage": stage}
    if isinstance(exc, ValidationError):
        details["issues"] = [
            {"path": list(item["loc"]), "type": item["type"]}
            for item in exc.errors(include_input=False, include_context=False)[:10]
        ]
    return AppError(
        "invalid_request",
        f"AG-UI input validation failed ({stage}).",
        422,
        details=details,
    )
