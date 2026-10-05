"""Regression coverage for safe native input diagnostics."""

import json

from pydantic import BaseModel, ValidationError

from app.agui.validation_errors import native_validation_error


def test_validation_error_reports_field_without_input():
    """Keep the useful field location but never echo its sensitive value."""
    class Input(BaseModel):
        """A minimal typed input for exercising Pydantic errors."""

        revision: int

    try:
        Input.model_validate({"revision": "private-token-value"})
    except ValidationError as exc:
        error = native_validation_error(exc, "state")
    payload = error.envelope("request-1")
    assert error.status_code == 422
    assert payload["error"]["details"] == {
        "stage": "state",
        "issues": [{"path": ["revision"], "type": "int_parsing"}],
    }
    assert "private-token-value" not in json.dumps(payload)


def test_plain_value_error_does_not_echo_exception_text():
    """Unexpected validator text must not expose request content."""
    error = native_validation_error(ValueError("private-data"), "tools")
    assert error.details == {"stage": "tools"}
    assert "private-data" not in json.dumps(error.envelope("request-2"))
