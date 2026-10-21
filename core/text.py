"""Text normalisation shared by storage and printed documents.

One rule, one place: identity data is stored in upper case, so a value typed
once at sign-up ("Maria") cannot appear as "MARIA" on one screen and "Maria"
on another, and the printed interview sheet always matches the record. Only
*formatting* happens here - a value is never reinterpreted, translated or
completed (agents.md rule 1).

Usernames and passwords are deliberately never passed through these helpers:
a username is a login credential and must stay exactly as typed.
"""


def uppercase(value) -> str:
    """``value`` stripped and upper-cased; ``None`` becomes an empty string."""
    if value is None:
        return ""
    return str(value).strip().upper()


def uppercase_fields(instance, names) -> list:
    """Upper-case the named fields on ``instance`` in place.

    Returns the names that actually changed, so a caller that persists with
    ``update_fields`` can pass exactly those.
    """
    changed = []
    for name in names:
        before = getattr(instance, name)
        after = uppercase(before)
        if after != before:
            setattr(instance, name, after)
            changed.append(name)
    return changed
