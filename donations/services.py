"""DonationService — coordinates donation lifecycle, collection and rewards.

Views call these methods; business rules never live in templates or views.
"""
import logging

from django.db import transaction
from django.utils import timezone

from donations.models import Donation

logger = logging.getLogger("bloodbank.donations")


class DonationError(Exception):
    pass


class DonationService:
    _AUDIT_ACTIONS = {
        "SCHEDULED": "DONATION_SCHEDULED",
        "REGISTERED": "DONATION_REGISTERED",
        "SCREENING": "DONATION_SCREENING_STARTED",
        "APPROVED": "DONATION_APPROVED",
        "COLLECTED": "DONATION_COLLECTED",
        "TESTING": "DONATION_TESTING",
        "RELEASED": "DONATION_RELEASED",
        "DEFERRED": "DONATION_DEFERRED",
        "REJECTED": "DONATION_REJECTED",
        "CANCELLED": "DONATION_CANCELLED",
    }

    @staticmethod
    def create(*, donor, actor=None, request=None, appointment=None, status="REGISTERED",
               donation_date=None, donation_time=None, location="", donation_type="VOLUNTARY",
               notes="", blood_type=None):
        from audit import services as audit

        with transaction.atomic():
            donation = Donation.objects.create(
                donor=donor,
                appointment=appointment,
                donation_date=donation_date or timezone.localdate(),
                donation_time=donation_time,
                location=location,
                donation_type=donation_type,
                notes=notes,
                staff=actor,
                status=status,
                blood_type=blood_type or donor.blood_type,
            )
            audit.log(request, user=actor, action="DONATION_CREATED", module="donations", obj=donation,
                      description=f"Donation {donation.donation_code} registered for donor {donor.donor_code}")
        return donation

    @staticmethod
    def transition(donation, new_status, *, actor=None, request=None, reason="", extra_audit=""):
        from audit import services as audit

        if not donation.can_transition_to(new_status):
            raise DonationError(
                f"Illegal donation transition {donation.status} → {new_status}. "
                f"Allowed: {', '.join(donation.TRANSITIONS.get(donation.status, [])) or 'none'}."
            )
        previous = donation.status
        with transaction.atomic():
            donation.status = new_status
            if reason:
                donation.notes = (donation.notes + f"\n[{timezone.now():%Y-%m-%d %H:%M}] {reason}").strip()
            donation.save()
            audit.log(request, user=actor,
                      action=DonationService._AUDIT_ACTIONS.get(new_status, "DONATION_STATUS_CHANGED"),
                      module="donations", obj=donation,
                      before={"status": previous}, after={"status": new_status},
                      description=f"Donation {donation.donation_code}: {previous} → {new_status}"
                                  + (f" — {reason}" if reason else "") + (f" {extra_audit}" if extra_audit else ""))
        return donation

    @staticmethod
    def record_collection(donation, *, component, volume_ml, collected_at=None, location="",
                          storage_position="", actor=None, request=None):
        """APPROVED → COLLECTED and register the blood bag (starts QUARANTINED).
        Awards configured donation points (if a rule exists). Atomic."""
        from audit import services as audit
        from inventory.services import InventoryService
        from rewards.services import RewardService

        if donation.status != "APPROVED":
            raise DonationError(
                f"Collection requires an APPROVED donation (donor cleared by screening); "
                f"current status is {donation.status}."
            )
        collected_at = collected_at or timezone.now()
        with transaction.atomic():
            DonationService.transition(donation, "COLLECTED", actor=actor, request=request,
                                       reason=f"Collected {volume_ml} mL at {collected_at:%Y-%m-%d %H:%M}")
            donation.volume_ml = volume_ml
            donation.donation_time = collected_at.time()
            donation.save(update_fields=["volume_ml", "donation_time", "updated_at"])

            bag = InventoryService.create_bag_from_donation(
                donation, component=component, volume_ml=volume_ml, collected_at=collected_at,
                location=location, storage_position=storage_position, actor=actor, request=request,
            )

            # Close out any linked appointment.
            appt = donation.appointment
            if appt and appt.status in ("REQUESTED", "CONFIRMED", "CHECKED_IN"):
                appt.status = "COMPLETED"
                appt.save(update_fields=["status"])

            # Reward points (configured rule; no-op when not configured).
            RewardService.award_for_event(donation.donor, "DONATION_COMPLETED",
                                          reference=f"donation:{donation.donation_code}",
                                          actor=actor, request=request)
        return donation, bag
