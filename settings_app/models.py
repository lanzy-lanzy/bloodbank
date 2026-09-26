from django.conf import settings
from django.db import models


class SystemSetting(models.Model):
    """Centralized, admin-editable configuration.

    All institution-specific values (eligibility rules, thresholds, expiration
    alert windows, organization details, reward values) live here rather than
    in code. Critical changes are audited by settings_app.views.
    """

    CATEGORY_CHOICES = [
        ("general", "General"),
        ("eligibility", "Donor Eligibility Rules"),
        ("inventory", "Inventory & Blood Bank"),
        ("notifications", "Notifications"),
        ("rewards", "Rewards"),
        ("security", "Security"),
    ]

    key = models.SlugField(max_length=100, unique=True)
    value = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="general", db_index=True)
    value_type = models.CharField(max_length=10, choices=[("str", "Text"), ("int", "Number"), ("bool", "Yes/No")], default="str")
    description = models.TextField(blank=True)
    is_sensitive = models.BooleanField(default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "key"]

    def __str__(self):
        return f"{self.key}={self.value}"

    def typed_value(self):
        if self.value_type == "int":
            try:
                return int(self.value)
            except (TypeError, ValueError):
                return None
        if self.value_type == "bool":
            return str(self.value).lower() in ("1", "true", "yes", "on")
        return self.value
