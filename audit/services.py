"""AuditService — the single entry point for writing audit events."""
import logging

from audit.models import AuditLog

logger = logging.getLogger("bloodbank.audit")


def _client_ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _snapshot(obj, fields=None):
    """Best-effort serialisable snapshot of a model instance."""
    if obj is None:
        return None
    data = {}
    for field in obj._meta.fields:
        if fields and field.name not in fields:
            continue
        value = getattr(obj, field.name, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif hasattr(value, "pk"):
            value = value.pk
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            value = str(value)  # UUID, Decimal, etc. — keep JSON-serialisable
        data[field.name] = value
    return data


def log(
    request=None,
    *,
    user=None,
    action: str,
    module: str,
    obj=None,
    object_type: str = "",
    object_id: str = "",
    before=None,
    after=None,
    description: str = "",
    snapshot_fields=None,
):
    """Append an audit event. Never raises for ordinary use — audit failures are
    logged loudly but must not corrupt the surrounding business transaction's
    intent; callers inside atomic blocks should treat audit as part of the tx."""
    actor = user or (request.user if request is not None and request.user.is_authenticated else None)
    if obj is not None:
        object_type = object_type or obj.__class__.__name__
        object_id = object_id or str(obj.pk)
        if after is None and before is None:
            after = _snapshot(obj, snapshot_fields)
    entry = AuditLog(
        user=actor,
        action=action,
        module=module,
        object_type=object_type,
        object_id=str(object_id),
        ip_address=_client_ip(request),
        before_state=before,
        after_state=after,
        description=description,
    )
    entry.save()
    logger.info("AUDIT %s %s/%s by %s", action, module, object_id, actor)
    return entry
