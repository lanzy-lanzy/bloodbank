"""Shared field validators."""
import re

from django.core.exceptions import ValidationError


def normalize_ph_mobile(raw):
    """Normalize a Philippine mobile number to local SMS format 09XXXXXXXXX.

    Accepts 09…, +63…, 63… with spaces/dashes. Returns None when the digits
    cannot form a Philippine mobile number. Single source of truth for both
    form validation and SMS provider dispatch."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 11 and digits.startswith("09"):
        return digits
    return None


def validate_ph_mobile(value):
    if normalize_ph_mobile(value) is None:
        raise ValidationError(
            "Enter a valid Philippine mobile number (e.g. 09171234567 or +639171234567).",
            code="invalid_ph_mobile",
        )
