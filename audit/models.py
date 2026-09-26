from django.conf import settings
from django.db import models


class ImmutableAuditQuerySet(models.QuerySet):
    """Queryset-level immutability: queryset .delete()/.update() bypass the
    model's per-instance delete(), so bulk mutation must be blocked here too."""

    def delete(self, *args, **kwargs):
        raise RuntimeError("AuditLog records are immutable and cannot be deleted (bulk).")

    def update(self, *args, **kwargs):
        raise RuntimeError("AuditLog records are immutable and cannot be updated (bulk).")


class AuditLog(models.Model):
    """Immutable audit trail.

    Records are append-only: updating or deleting an existing record raises a
    RuntimeError so history can never be silently rewritten. Django admin and
    app views expose it read-only.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_events",
    )
    objects = ImmutableAuditQuerySet.as_manager()
    action = models.CharField(max_length=64, db_index=True)  # e.g. DONOR_CREATED
    module = models.CharField(max_length=32, db_index=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module", "action"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action} {self.object_type} {self.object_id}".strip()

    # --- immutability ------------------------------------------------------------
    def save(self, *args, **kwargs):
        if self.pk is not None and AuditLog.objects.filter(pk=self.pk).exists():
            raise RuntimeError("AuditLog records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("AuditLog records are immutable and cannot be deleted.")
