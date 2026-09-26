"""RegistrationService — self-registration submission and admin review workflow.

All business rules for registrations live here (never in views/templates):
role allow-list, one-shot PENDING -> APPROVED/REJECTED transitions,
self-approval guard, inactive-until-approved account posture.
"""
import logging

from django.db import transaction
from django.utils import timezone

from accounts.models import RegistrationRequest, User
from audit import services as audit

logger = logging.getLogger("bloodbank.accounts")


class RegistrationError(Exception):
    pass


class RegistrationService:
    ELIGIBLE_ROLES = {User.Role.DONOR, User.Role.REQUESTER}

    @staticmethod
    def submit(*, registration, raw_password, request=None):
        """Create the inactive User + PENDING registration, notify admins, audit."""
        from notifications.services import NotificationError, NotificationService

        if registration.role not in RegistrationService.ELIGIBLE_ROLES:
            raise RegistrationError(
                "Self-registration is only allowed for DONOR or REQUESTER roles.")
        with transaction.atomic():
            user = User.objects.create_user(
                username=registration.username, password=raw_password,
                email=registration.email, phone=registration.phone,
                first_name=registration.first_name, middle_name=registration.middle_name,
                last_name=registration.last_name, role=registration.role,
                is_active=False)
            registration.user = user
            # registration may still be unsaved here (ModelForm commit=False),
            # so this is an INSERT, not a field-limited UPDATE.
            registration.save()
            audit.log(request, action="REGISTRATION_SUBMITTED", module="accounts",
                      obj=registration,
                      description=f"{registration.get_role_display()} registration submitted "
                                  f"by {registration.full_name} ({registration.username}). "
                                  "Account is inactive until an administrator approves it.")
        # Notify every active admin (in-app + SMS channel per template). SMS to
        # unconfigured numbers fails safe and is recorded per channel row.
        context = {"full_name": registration.full_name,
                   "role": registration.get_role_display()}
        for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True):
            try:
                NotificationService.send_to_user(admin, "registration_submitted", context)
            except NotificationError as exc:
                logger.warning("Admin registration alert failed for %s: %s", admin.username, exc)
        return registration

    @staticmethod
    def review(registration, *, decision, actor, reason="", request=None):
        """Approve or reject a PENDING registration. One-shot; audited; notifies applicant."""
        from notifications.services import NotificationError, NotificationService

        if decision not in (RegistrationRequest.Status.APPROVED,
                            RegistrationRequest.Status.REJECTED):
            raise RegistrationError("Decision must be APPROVED or REJECTED.")
        if registration.status != RegistrationRequest.Status.PENDING:
            raise RegistrationError("This registration has already been reviewed.")
        if registration.user_id is None:
            raise RegistrationError("This registration has no linked user account.")
        if registration.user_id == actor.pk:
            raise RegistrationError("You cannot review your own registration.")
        if decision == RegistrationRequest.Status.REJECTED and not (reason or "").strip():
            raise RegistrationError("A rejection reason is required.")

        user = registration.user
        before = {"status": registration.status, "is_active": user.is_active}
        with transaction.atomic():
            registration.status = decision
            registration.reviewed_by = actor
            registration.reviewed_at = timezone.now()
            registration.rejection_reason = reason.strip() if decision == RegistrationRequest.Status.REJECTED else ""
            registration.save(update_fields=["status", "reviewed_by", "reviewed_at",
                                             "rejection_reason"])
            if decision == RegistrationRequest.Status.APPROVED:
                user.is_active = True
                user.save(update_fields=["is_active"])
            audit.log(request, user=actor,
                      action="REGISTRATION_APPROVED" if decision == RegistrationRequest.Status.APPROVED
                      else "REGISTRATION_REJECTED",
                      module="accounts", obj=registration, before=before,
                      after={"status": registration.status, "is_active": user.is_active},
                      description=f"{registration.get_role_display()} registration of "
                                  f"{registration.username} "
                                  f"{'approved' if decision == RegistrationRequest.Status.APPROVED else 'rejected'}"
                                  + (f" — reason: {reason.strip()}" if reason else ""))
        context = {"full_name": registration.full_name,
                   "role": registration.get_role_display(),
                   "rejection_reason": registration.rejection_reason}
        template = ("registration_approved" if decision == RegistrationRequest.Status.APPROVED
                    else "registration_rejected")
        try:
            NotificationService.send_to_user(user, template, context,
                                             channels=["in_app", "sms"], actor=actor)
        except NotificationError as exc:
            logger.warning("Applicant review notification failed for %s: %s",
                           registration.username, exc)
        return registration
