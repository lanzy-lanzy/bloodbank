"""DonorEligibilityService — configurable eligibility engine.

SAFETY: The *mechanism* lives here; the *values* (intervals, age limits,
weight minimums, deferral periods) are institutional configuration stored in
SystemSetting (category "eligibility"). Nothing clinical is hard-coded. When a
rule value is not configured, the engine returns REQUIRES_STAFF_REVIEW rather
than inventing a threshold. Final eligibility is always a staff decision —
the engine only summarises configured rules.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

# SystemSetting keys used by the engine (values set by the institution):
ELIGIBILITY_KEYS = {
    "min_donation_interval_days": ("int", "Minimum days between whole-blood donations"),
    "min_age_years": ("int", "Minimum donor age"),
    "max_age_years": ("int", "Maximum donor age"),
    "min_weight_kg": ("int", "Minimum donor weight (kg), checked at screening"),
    "max_donations_per_year": ("int", "Maximum donations per rolling 12 months"),
}


@dataclass
class EligibilityResult:
    status: str  # ELIGIBLE | NOT_ELIGIBLE | DEFERRED | REQUIRES_STAFF_REVIEW
    reasons: list = field(default_factory=list)
    next_eligible_date: object = None

    @property
    def is_eligible(self):
        return self.status == "ELIGIBLE"


class DonorEligibilityService:
    @staticmethod
    def _rule(key):
        from settings_app.services import get_setting
        return get_setting(key, None)

    @staticmethod
    def evaluate(donor, when=None) -> EligibilityResult:
        when = when or timezone.now()
        today = when.date() if hasattr(when, "date") else when
        reasons = []
        review_reasons = []

        # 1. Donor status (institutional decision recorded on the donor)
        if donor.status == Donor_Status.PERM_DEFERRED:
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Permanently deferred. {donor.deferral_reason}".strip()])
        if donor.status == Donor_Status.BLACKLISTED:
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Restricted/blacklisted. {donor.deferral_reason}".strip()])
        if donor.status == Donor_Status.INACTIVE:
            return EligibilityResult("NOT_ELIGIBLE", ["Donor record is inactive."])
        if donor.status == Donor_Status.TEMP_DEFERRED:
            until = donor.deferred_until
            if until and until > today:
                return EligibilityResult("DEFERRED",
                                         [f"Temporarily deferred until {until:%b %d, Y}. {donor.deferral_reason}".strip()],
                                         next_eligible_date=until)
            reasons.append("Previous temporary deferral period has passed — staff review advised.")

        # 2. Latest screening outcome
        latest_screening = donor.screenings.order_by("-performed_at").first()
        if latest_screening:
            if latest_screening.result == "REJECTED":
                return EligibilityResult("NOT_ELIGIBLE",
                                         [f"Rejected at screening on {latest_screening.performed_at:%b %d, Y}."])
            if latest_screening.result == "REQUIRES_REVIEW":
                review_reasons.append("Latest screening outcome is 'Requires review'.")
            if latest_screening.result == "DEFERRED" and latest_screening.deferral_days:
                until = (latest_screening.performed_at + timedelta(days=latest_screening.deferral_days)).date()
                if until > today:
                    return EligibilityResult("DEFERRED",
                                             [f"Deferred by screening until {until:%b %d, Y} ({latest_screening.deferral_days} days)."],
                                             next_eligible_date=until)

        # 3. Age window (configured)
        min_age = DonorEligibilityService._rule("min_age_years")
        max_age = DonorEligibilityService._rule("max_age_years")
        if min_age is not None and donor.age < int(min_age):
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Donor is {donor.age}; configured minimum age is {min_age}."])
        if max_age is not None and donor.age > int(max_age):
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Donor is {donor.age}; configured maximum age is {max_age}."])

        # 4. Donation interval (configured)
        interval = DonorEligibilityService._rule("min_donation_interval_days")
        last = donor.last_donation
        next_eligible = None
        if last:
            if interval is None:
                review_reasons.append(
                    "Minimum donation interval is not configured; cannot verify interval since last donation "
                    f"({last.donation_date:%b %d, Y})."
                )
            else:
                next_eligible = last.donation_date + timedelta(days=int(interval))
                if next_eligible > today:
                    return EligibilityResult(
                        "DEFERRED",
                        [f"Last donation {last.donation_date:%b %d, Y}; configured interval is {interval} days."],
                        next_eligible_date=next_eligible,
                    )

        # 5. Rolling-year donation cap (configured, optional)
        cap = DonorEligibilityService._rule("max_donations_per_year")
        if cap is not None:
            since = today - timedelta(days=365)
            count = donor.donations.filter(
                status__in=["COLLECTED", "RELEASED"], donation_date__gte=since
            ).count()
            if count >= int(cap):
                return EligibilityResult(
                    "NOT_ELIGIBLE",
                    [f"Donor has {count} donation(s) in the past 12 months; configured maximum is {cap}."],
                )

        if review_reasons:
            return EligibilityResult("REQUIRES_STAFF_REVIEW", review_reasons + reasons, next_eligible_date=next_eligible)
        return EligibilityResult("ELIGIBLE", reasons or ["All configured eligibility rules satisfied."],
                                 next_eligible_date=next_eligible)


# Local alias to avoid importing Donor at module import time (app-loading order).
class Donor_Status:
    ACTIVE = "ACTIVE"
    TEMP_DEFERRED = "TEMP_DEFERRED"
    PERM_DEFERRED = "PERM_DEFERRED"
    INACTIVE = "INACTIVE"
    BLACKLISTED = "BLACKLISTED"
