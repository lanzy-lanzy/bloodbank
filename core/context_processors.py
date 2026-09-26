"""Template context available site-wide."""


def site_settings(request):
    # Imported lazily to avoid app-loading cycles.
    from settings_app.services import get_setting

    return {
        "ORG_NAME": get_setting("organization_name", "Blood Bank Management System"),
        "ORG_ADDRESS": get_setting("organization_address", ""),
        "ORG_CONTACT": get_setting("organization_contact", ""),
    }
