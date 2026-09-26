"""Accessors for SystemSetting with safe fallbacks."""
from settings_app.models import SystemSetting


def get_setting(key: str, default=None):
    setting = SystemSetting.objects.filter(key=key).first()
    if setting is None or setting.value in ("", None):
        return default
    value = setting.typed_value()
    return default if value is None else value


def get_int_setting(key: str, default: int | None = None) -> int | None:
    value = get_setting(key, None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_bool_setting(key: str, default: bool = False) -> bool:
    value = get_setting(key, None)
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def set_setting(key: str, value, *, user=None, category="general", value_type="str",
                description="", audit_request=None):
    """Create-or-update a setting; audited when a request/actor is supplied."""
    setting, created = SystemSetting.objects.get_or_create(
        key=key, defaults={"category": category, "value_type": value_type, "description": description}
    )
    before = None if created else setting.value
    setting.value = str(value)
    setting.updated_by = user
    setting.save()
    if user is not None:
        from audit import services as audit
        audit.log(audit_request, user=user, action="SYSTEM_SETTING_CHANGED", module="settings",
                  obj=setting, before={"value": before}, after={"value": setting.value},
                  description=f"Setting '{key}' changed from {before!r} to {setting.value!r}")
    return setting
