"""Seed demonstration data for the Blood Bank Management System.

Everything created by this command is DEMO data. Values that are clinically
or institutionally significant (eligibility thresholds, compatibility rules,
shelf lives, required tests, reward points) are seeded as clearly-labelled
placeholders and MUST be reviewed / replaced by the responsible institution
before any real use.

Idempotent: safe to re-run; existing rows are never overwritten or deleted.
This command never deletes data.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

DEMO_NOTE = "DEMO placeholder — requires institutional validation/approval."

# Standard ABO/Rh red-cell compatibility matrix — seeded UNAPPROVED
# (approved_by is left empty) so an administrator must explicitly review it.
RBC_COMPAT = {
    "O-": ["O-"],
    "O+": ["O-", "O+"],
    "A-": ["A-", "O-"],
    "A+": ["A+", "A-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "AB-": ["AB-", "A-", "B-", "O-"],
    "AB+": ["AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"],
}


class Command(BaseCommand):
    help = "Seed demo configuration and sample records (never deletes data)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo12345!",
                            help="Password for demo accounts (default: Demo12345!).")
        parser.add_argument("--no-clinical-demo", action="store_true",
                            help="Skip seeding eligibility thresholds and compatibility rules "
                                 "(system will then return REQUIRES STAFF REVIEW / 'not compatible' "
                                 "until an administrator configures them).")
        parser.add_argument("--no-sample-records", action="store_true",
                            help="Seed configuration only; skip demo donors/donations/requests.")

    @transaction.atomic
    def handle(self, *args, **options):
        self.password = options["password"]
        self.clinical = not options["no_clinical_demo"]
        self.samples = not options["no_sample_records"]

        self.users = self._seed_users()
        self.blood_types = self._seed_blood_types()
        self.components = self._seed_components()
        self.test_types = self._seed_test_types()
        self._seed_questions()
        self._seed_settings()
        self._seed_templates()
        self._seed_rewards()
        if self.clinical:
            self._seed_compatibility()
        else:
            self.stdout.write(self.style.WARNING(
                "Skipping compatibility/eligibility demo values (--no-clinical-demo)."))
        if self.samples:
            self._seed_sample_records()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seed complete."))
        self.stdout.write("Demo accounts (password: %s):" % self.password)
        for username, role in [("admin", "ADMIN"), ("staff", "STAFF"),
                               ("donor", "DONOR"), ("requester", "REQUESTER")]:
            self.stdout.write(f"  {username:<10} {role}")
        if self.clinical:
            self.stdout.write(self.style.WARNING(
                "\nWARNING: eligibility thresholds, compatibility rules, shelf lives,\n"
                "required tests and reward values are DEMO placeholders. A qualified\n"
                "administrator / the responsible institution MUST review and approve\n"
                "them (Settings -> Blood Bank, Settings -> Eligibility) before real use."))

    # ------------------------------------------------------------------ users
    def _seed_users(self):
        from accounts.models import User

        # Valid-format demo mobiles: ProfileForm requires a reachable PH
        # mobile number so SMS notification flows can be exercised.
        # Backfill only — an existing non-blank phone is never overwritten.
        demo_phones = {"admin": "09170000011", "staff": "09170000012",
                       "donor": "09170000013", "requester": "09170000014"}

        def backfill_phone(user):
            if not user.phone:
                user.phone = demo_phones[user.username]
                user.save(update_fields=["phone"])
                self.stdout.write(f"Filled demo phone for user '{user.username}'.")

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"role": "ADMIN", "email": "admin@example.test",
                      "first_name": "Ada", "last_name": "Admin", "is_staff": True,
                      "is_superuser": True, "phone": demo_phones["admin"]},
        )
        if created:
            admin.set_password(self.password)
            admin.save()
            self.stdout.write("Created admin user 'admin'.")
        else:
            backfill_phone(admin)

        staff, created = User.objects.get_or_create(
            username="staff",
            defaults={"role": "STAFF", "email": "staff@example.test",
                      "first_name": "Sam", "last_name": "Staff", "is_staff": True,
                      "phone": demo_phones["staff"]},
        )
        if created:
            staff.set_password(self.password)
            staff.save()
            self.stdout.write("Created staff user 'staff'.")
        else:
            backfill_phone(staff)

        donor_user, created = User.objects.get_or_create(
            username="donor",
            defaults={"role": "DONOR", "email": "donor@example.test",
                      "first_name": "Diana", "last_name": "Donor",
                      "phone": demo_phones["donor"]},
        )
        if created:
            donor_user.set_password(self.password)
            donor_user.save()
            self.stdout.write("Created donor user 'donor'.")
        else:
            backfill_phone(donor_user)

        requester_user, created = User.objects.get_or_create(
            username="requester",
            defaults={"role": "REQUESTER", "email": "requester@example.test",
                      "first_name": "Rhea", "last_name": "Requester",
                      "phone": demo_phones["requester"]},
        )
        if created:
            requester_user.set_password(self.password)
            requester_user.save()
            self.stdout.write("Created requester user 'requester'.")
        else:
            backfill_phone(requester_user)

        return {"admin": admin, "staff": staff, "donor": donor_user, "requester": requester_user}

    # -------------------------------------------------------------- inventory
    def _seed_blood_types(self):
        from inventory.models import BloodType

        types = {}
        for abo, rh in [("A", "POS"), ("A", "NEG"), ("B", "POS"), ("B", "NEG"),
                        ("AB", "POS"), ("AB", "NEG"), ("O", "POS"), ("O", "NEG")]:
            bt, _ = BloodType.objects.get_or_create(abo=abo, rh=rh)
            types[bt.code] = bt
        self.stdout.write(f"Blood types present: {len(types)}")
        return types

    def _seed_components(self):
        from inventory.models import BloodComponent

        defs = [
            ("Whole Blood", "wb", 35),
            ("Packed Red Blood Cells", "prbc", 42),
            ("Platelets", "plt", 5),
            ("Fresh Frozen Plasma", "ffp", 365),
            ("Cryoprecipitate", "cryo", 365),
        ]
        components = {}
        for name, code, days in defs:
            comp, created = BloodComponent.objects.get_or_create(
                code=code,
                defaults={"name": name, "default_shelf_life_days": days,
                          "description": f"{DEMO_NOTE} Shelf life is a placeholder."},
            )
            components[code] = comp
        self.stdout.write(f"Blood components present: {len(components)}")
        return components

    def _seed_test_types(self):
        from inventory.models import TestType

        defs = [
            ("ABO Grouping", "GROUPING", True),
            ("Rh Typing", "RH", True),
            ("Infectious Disease Screening", "INFECTIOUS", True),
        ]
        tests = {}
        for name, category, required in defs:
            tt, _ = TestType.objects.get_or_create(
                name=name,
                defaults={"category": category, "is_required": required},
            )
            tests[name] = tt
        self.stdout.write(self.style.WARNING(
            f"Test types present: {len(tests)} — the required-test set gates blood bag release "
            f"and MUST be defined by the institution ({DEMO_NOTE})"))
        return tests

    def _seed_compatibility(self):
        from inventory.models import CompatibilityRule

        created_count = 0
        for patient_code, donor_codes in RBC_COMPAT.items():
            patient = self.blood_types[patient_code]
            for donor_code in self.blood_types.keys():
                rule, created = CompatibilityRule.objects.get_or_create(
                    patient_type=patient, donor_type=self.blood_types[donor_code], component=None,
                    defaults={
                        "is_allowed": donor_code in donor_codes,
                        # approved_by intentionally left NULL: rules are
                        # UNAPPROVED until an administrator reviews them.
                        "note": "DEMO — standard ABO/Rh red-cell table, UNVALIDATED. "
                                "Component-specific rules (e.g. plasma) differ; not included.",
                    },
                )
                created_count += int(created)
        self.stdout.write(self.style.WARNING(
            f"Compatibility rules seeded: {created_count} new (UNAPPROVED — admin must review "
            f"in Settings -> Blood Bank -> Compatibility)."))

    # ----------------------------------------------------------------- donors
    def _seed_questions(self):
        from donors.models import ScreeningQuestion

        defs = [
            ("Are you feeling well today?", "General", "YESNO", 1),
            ("Have you had any illness, fever, or medical treatment recently?", "Medical history", "YESNO", 2),
            ("Have you donated blood recently?", "Donation history", "YESNO", 3),
            ("Do you have any known allergies or current medications?", "Medical history", "TEXT", 4),
            ("Have you traveled out of the province in the last month?", "General", "YESNO", 5),
        ]
        for text, category, answer_type, order in defs:
            ScreeningQuestion.objects.get_or_create(
                text=text,
                defaults={"category": category, "answer_type": answer_type,
                          "order": order, "required": True},
            )
        self.stdout.write(self.style.WARNING(
            f"Screening questions present: {ScreeningQuestion.objects.count()} "
            f"({DEMO_NOTE} Replace with the institution's questionnaire.)"))

    # --------------------------------------------------------------- settings
    def _seed_settings(self):
        from settings_app.models import SystemSetting
        from settings_app.services import set_setting

        def only_if_missing(key, value, category, value_type, description):
            if not SystemSetting.objects.filter(key=key).exists():
                set_setting(key, value, category=category, value_type=value_type,
                            description=description)

        only_if_missing("org_name", "Demo Blood Bank", "general", "str",
                        "Organization name shown across the UI.")
        only_if_missing("org_address", "123 Demo Street, Zamboanga City", "general", "str", "")
        only_if_missing("org_contact", "+63 62 000 0000", "general", "str", "")
        only_if_missing("low_stock_threshold", 3, "inventory", "int",
                        "Available bags at or below this count flag a blood type as low stock.")
        only_if_missing("expiring_soon_days", 7, "inventory", "int",
                        "Bags expiring within this many days are listed as 'expiring soon'.")

        if self.clinical:
            only_if_missing("min_donation_interval_days", 90, "eligibility", "int",
                            f"{DEMO_NOTE} Must be set/approved by institutional medical authority.")
            only_if_missing("min_age_years", 18, "eligibility", "int",
                            f"{DEMO_NOTE} Must be set/approved by institutional medical authority.")
            only_if_missing("max_age_years", 65, "eligibility", "int",
                            f"{DEMO_NOTE} Must be set/approved by institutional medical authority.")
            only_if_missing("min_weight_kg", 50, "eligibility", "int",
                            f"{DEMO_NOTE} Must be set/approved by institutional medical authority.")
            only_if_missing("max_donations_per_year", 4, "eligibility", "int",
                            f"{DEMO_NOTE} Must be set/approved by institutional medical authority.")
        self.stdout.write(f"System settings present: {SystemSetting.objects.count()}")

    # ---------------------------------------------------------- notifications
    def _seed_templates(self):
        from notifications.models import NotificationTemplate

        defs = [
            ("emergency_alert", "Emergency Blood Request — {{ blood_type }}",
             "Dear {{ donor_name }}, an EMERGENCY request for {{ quantity }} unit(s) of "
             "{{ blood_type }} {{ component }} was issued by {{ organization }} "
             "(request {{ request_id }}, needed by {{ required_by }}). "
             "If you are available to donate, please respond: {{ response_url }} "
             "Your response indicates availability only — standard screening still applies.",
             "in_app,sms"),
            ("emergency_request_alert", "Emergency request {{ request_id }}",
             "Emergency blood request {{ request_id }} from {{ organization }} "
             "(urgency: {{ urgency }}) is awaiting staff action.",
             "in_app"),
            ("request_fulfilled", "Request {{ request_id }} fulfilled",
             "Blood request {{ request_id }} for {{ organization }} has been fulfilled.",
             "in_app"),
            ("appointment_reminder", "Donation appointment reminder",
             "Dear {{ donor_name }}, this is a reminder of your donation appointment on "
             "{{ appointment_date }} at {{ appointment_time }} ({{ location }}).",
             "in_app,sms"),
            ("expiration_alert", "Blood bags expiring soon",
             "{{ quantity }} blood bag(s) will expire within the configured alert window. "
             "Review inventory: {{ bag_code }}.",
             "in_app"),
            ("donation_thank_you", "Thank you for donating",
             "Dear {{ donor_name }}, thank you for your donation ({{ donation_code }}). "
             "You earned {{ points }} point(s).",
             "in_app"),
            ("reward_earned", "You reached a new reward tier",
             "Dear {{ donor_name }}, congratulations — you reached tier {{ tier_name }} "
             "with {{ points }} points.",
             "in_app"),
            ("registration_submitted", "New {{ role }} registration — {{ full_name }}",
             "A new {{ role }} registration from {{ full_name }} is awaiting review. "
             "Open Registrations to approve or reject it.",
             "in_app,sms"),
            ("registration_approved", "Registration approved",
             "Dear {{ full_name }}, your registration has been approved — you can now "
             "sign in to the blood bank system.",
             "in_app,sms"),
            ("registration_rejected", "Registration not approved",
             "Dear {{ full_name }}, your registration was not approved. "
             "Reason: {{ rejection_reason }} You may contact the blood bank for details.",
             "in_app,sms"),
        ]
        for code, subject, body, channels in defs:
            NotificationTemplate.objects.get_or_create(
                code=code,
                defaults={"name": code.replace("_", " ").title(), "subject": subject,
                          "body": body, "channels": channels,
                          "description": "Seeded by seed_demo; edit under Notifications -> Templates."},
            )
        self.stdout.write(f"Notification templates present: {NotificationTemplate.objects.count()}")

    # ---------------------------------------------------------------- rewards
    def _seed_rewards(self):
        from rewards.models import Reward, RewardRule, RewardTier

        rule_defs = [
            # Code must match the literal passed by DonationService.record_collection.
            ("DONATION_COMPLETED", "DONATION_COMPLETED", 100, None),
            ("EMERGENCY_DONATION", "EMERGENCY_DONATION", 50, None),
            ("MILESTONE", "MILESTONE", 200, 5),
        ]
        for code, event, points, every_n in rule_defs:
            RewardRule.objects.get_or_create(
                code=code,
                defaults={"event": event, "points": points, "milestone_every_n": every_n or 0,
                          "description": f"{DEMO_NOTE} Point values are placeholders."},
            )
        tier_defs = [("Bronze", 0, 1), ("Silver", 500, 2), ("Gold", 1500, 3), ("Platinum", 3000, 4)]
        for name, min_points, order in tier_defs:
            RewardTier.objects.get_or_create(
                name=name, defaults={"min_points": min_points, "order": order,
                                     "description": f"{DEMO_NOTE}"})
        reward_defs = [
            ("Certificate of Appreciation", 100, None),
            ("Blood Bank T-Shirt", 500, 50),
            ("Commemorative Mug", 300, 100),
        ]
        for name, cost, stock in reward_defs:
            Reward.objects.get_or_create(
                name=name, defaults={"points_cost": cost, "stock": stock,
                                     "description": f"{DEMO_NOTE}"})
        self.stdout.write(self.style.WARNING(
            "Reward rules/tiers/rewards present — point values are DEMO placeholders."))

    # --------------------------------------------------------- sample records
    def _seed_sample_records(self):
        from accounts.models import User
        from appointments.models import Appointment
        from donations.services import DonationService
        from donors.models import Donor, DonorScreening
        from inventory.models import BloodBag, TestResult
        from inventory.services import InventoryService
        from requests.models import (BloodRequest, Organization, RequestItem,
                                     RequesterProfile)

        admin = self.users["admin"]
        staff = self.users["staff"]
        donor_user = self.users["donor"]
        requester_user = self.users["requester"]
        bt = self.blood_types
        wb = self.components["wb"]
        prbc = self.components["prbc"]
        now = timezone.now()
        today = timezone.localdate()

        # --- organization + requester profile -------------------------------
        org, _ = Organization.objects.get_or_create(
            name="Demo Provincial Hospital",
            defaults={"org_type": "HOSPITAL", "address": "45 Demo Ave, Zamboanga City",
                      "contact_number": "+63 62 111 2222", "email": "hospital@example.test",
                      "license_number": "DEMO-LIC-0001"},
        )
        RequesterProfile.objects.get_or_create(
            user=requester_user,
            defaults={"organization": org, "position": "Medical Technologist",
                      "is_authorized": True},
        )

        # --- donors ----------------------------------------------------------
        donor_defs = [
            ("Diana", "Donor", "O+", "MALE", today - timedelta(days=30 * 28), "09170000001",
             "donor@example.test", "Zamboanga City", Donor.Status.ACTIVE, donor_user),
            ("Jose", "Rizal", "O-", "MALE", today - timedelta(days=365 * 25), "09170000002",
             "jose@example.test", "Zamboanga City", Donor.Status.ACTIVE, None),
            ("Maria", "Clara", "A+", "FEMALE", today - timedelta(days=365 * 30), "09170000003",
             "maria@example.test", "Pagadian City", Donor.Status.ACTIVE, None),
            ("Juan", "Dela Cruz", "B+", "MALE", today - timedelta(days=365 * 22), "09170000004",
             "juan@example.test", "Dipolog City", Donor.Status.ACTIVE, None),
            ("Ana", "Santos", "AB+", "FEMALE", today - timedelta(days=365 * 27), "09170000005",
             "ana@example.test", "Zamboanga City", Donor.Status.ACTIVE, None),
            ("Pedro", "Penduko", "A-", "MALE", today - timedelta(days=365 * 35), "09170000006",
             "pedro@example.test", "Zamboanga City", Donor.Status.TEMP_DEFERRED, None),
            ("Liza", "Reyes", "B-", "FEMALE", today - timedelta(days=365 * 24), "09170000007",
             "liza@example.test", "Pagadian City", Donor.Status.ACTIVE, None),
            ("Carlo", "Garcia", "O+", "MALE", today - timedelta(days=365 * 29), "09170000008",
             "carlo@example.test", "Dipolog City", Donor.Status.ACTIVE, None),
        ]
        donors = {}
        for first, last, type_code, sex, dob, phone, email, muni, status, user in donor_defs:
            donor, _ = Donor.objects.get_or_create(
                first_name=first, last_name=last, date_of_birth=dob,
                defaults={
                    "sex": sex, "contact_number": phone, "email": email,
                    "municipality": muni, "province": "Zamboanga del Sur",
                    "address": f"Demo Street, {muni}",
                    "emergency_contact_name": f"EC of {first}",
                    "emergency_contact_phone": "09179999999",
                    "blood_type": bt[type_code], "status": status,
                    "user": user,
                    "deferral_reason": "Demo deferral (recent travel)" if status == Donor.Status.TEMP_DEFERRED else "",
                    "deferred_until": today + timedelta(days=30) if status == Donor.Status.TEMP_DEFERRED else None,
                },
            )
            donors[f"{first} {last}"] = donor
        self.stdout.write(f"Donors present: {Donor.objects.count()}")

        # --- completed donations -> tested, released bags ---------------------
        completed = [
            ("Jose Rizal", wb, 30),    # released, available
            ("Maria Clara", wb, 25),   # released, available
            ("Juan Dela Cruz", prbc, 12),  # released, available (expiring soon if shelf life short)
            ("Ana Santos", wb, 3),     # released, available — will be RESERVED for approved request
            ("Carlo Garcia", wb, 18),  # released, then force-expired (backdated expiry)
        ]
        released_bags = []
        for name, component, days_ago in completed:
            donor = donors[name]
            existing = donor.donations.filter(status__in=["COLLECTED", "TESTING", "RELEASED"]).first()
            if existing and existing.blood_bags.exists():
                released_bags.extend(list(existing.blood_bags.all()))
                continue
            collected_at = now - timedelta(days=days_ago)
            donation = DonationService.create(
                donor=donor, actor=staff, donation_date=collected_at.date(),
                location="Demo Blood Center", notes="Seeded demo donation.",
            )
            DonationService.transition(donation, "SCREENING", actor=staff)
            DonorScreening.objects.get_or_create(
                donation=donation,
                defaults={"donor": donor, "performed_by": staff, "performed_at": collected_at,
                          "identity_verified": True, "consent_given": True,
                          "weight_kg": Decimal("62.0"), "temperature_c": Decimal("36.8"),
                          "bp_systolic": 118, "bp_diastolic": 76, "pulse_bpm": 72,
                          "hemoglobin_gdl": Decimal("14.2"),
                          "result": DonorScreening.Result.CLEARED,
                          "notes": "Demo screening (values are fictional)."},
            )
            DonationService.transition(donation, "APPROVED", actor=staff)
            _donation, bag = DonationService.record_collection(
                donation, component=component, volume_ml=450, collected_at=collected_at,
                location="Demo Blood Center — Fridge A", storage_position=f"Shelf {donor.pk}",
                actor=staff,
            )
            # Required tests: performed by staff, verified by a second person (admin).
            for tt in self.test_types.values():
                if tt.is_required:
                    TestResult.objects.get_or_create(
                        bag=bag, test_type=tt,
                        defaults={"result": "Demo result",
                                  "result_status": TestResult.ResultStatus.NON_REACTIVE,
                                  "performed_by": staff, "performed_at": collected_at + timedelta(hours=4),
                                  "verified_by": admin, "verified_at": collected_at + timedelta(hours=6),
                                  "notes": "Demo test result (fictional)."},
                    )
            InventoryService.transition(bag.pk, "TESTING", actor=staff,
                                        reason="Demo: moved to testing")
            InventoryService.transition(bag.pk, "AVAILABLE", actor=admin,
                                        reason="Demo: authorized release after verified non-reactive tests")
            DonationService.transition(donation, "TESTING", actor=staff)
            DonationService.transition(donation, "RELEASED", actor=admin)
            released_bags.append(bag)
        self.stdout.write(f"Blood bags present: {BloodBag.objects.count()}")

        # --- bags still in the pipeline --------------------------------------
        if not BloodBag.objects.filter(status="QUARANTINED").exists():
            donor = donors["Liza Reyes"]
            donation = DonationService.create(
                donor=donor, actor=staff, location="Demo Blood Center",
                notes="Seeded demo donation (awaiting testing).")
            DonationService.transition(donation, "SCREENING", actor=staff)
            DonorScreening.objects.get_or_create(
                donation=donation,
                defaults={"donor": donor, "performed_by": staff,
                          "identity_verified": True, "consent_given": True,
                          "weight_kg": Decimal("55.5"), "temperature_c": Decimal("36.6"),
                          "bp_systolic": 110, "bp_diastolic": 70, "pulse_bpm": 68,
                          "result": DonorScreening.Result.CLEARED,
                          "notes": "Demo screening."},
            )
            DonationService.transition(donation, "APPROVED", actor=staff)
            DonationService.record_collection(
                donation, component=wb, volume_ml=450, collected_at=now - timedelta(hours=20),
                location="Demo Blood Center — Fridge A", actor=staff)

        # --- one platelet bag expiring soon (released; ~1 day left) ----------
        plt = self.components["plt"]
        if not BloodBag.objects.filter(component=plt, status="AVAILABLE").exists():
            bag = InventoryService.register_external_bag(
                blood_type=bt["AB+"], component=plt, volume_ml=250,
                collected_at=now - timedelta(days=4),
                location="Demo Blood Center — Platelet Bank",
                reason="Demo external bag (collection drive)", actor=staff)
            for tt in self.test_types.values():
                if tt.is_required:
                    TestResult.objects.get_or_create(
                        bag=bag, test_type=tt,
                        defaults={"result": "Demo result",
                                  "result_status": TestResult.ResultStatus.NON_REACTIVE,
                                  "performed_by": staff, "performed_at": now - timedelta(days=1),
                                  "verified_by": admin, "verified_at": now - timedelta(hours=20)},
                    )
            InventoryService.transition(bag.pk, "TESTING", actor=staff,
                                        reason="Demo: moved to testing")
            InventoryService.transition(bag.pk, "AVAILABLE", actor=admin,
                                        reason="Demo: authorized release")

        # --- one expired bag (release, then backdate expiry, then expire) ----        if not BloodBag.objects.filter(status="EXPIRED").exists() and released_bags:
            old = None
            for bag in released_bags:
                bag.refresh_from_db()
                if bag.status == "AVAILABLE" and bag.donor and bag.donor.full_name == "Carlo Garcia":
                    old = bag
                    break
            if old:
                old.expires_at = now - timedelta(hours=2)
                old.save(update_fields=["expires_at"])
                InventoryService.expire_bags(actor=admin)

        # --- appointments -----------------------------------------------------
        Appointment.objects.get_or_create(
            donor=donors["Diana Donor"], date=today + timedelta(days=2), time="09:00:00",
            defaults={"location": "Demo Blood Center", "status": Appointment.Status.CONFIRMED,
                      "created_by": staff, "notes": "Demo appointment."},
        )
        Appointment.objects.get_or_create(
            donor=donors["Jose Rizal"], date=today + timedelta(days=5), time="13:30:00",
            defaults={"location": "Demo Blood Center", "status": Appointment.Status.REQUESTED,
                      "created_by": donor_user, "notes": "Demo self-booked appointment."},
        )

        # --- blood requests ----------------------------------------------------
        # 1) routine SUBMITTED
        if not BloodRequest.objects.filter(urgency="ROUTINE").exists():
            req = BloodRequest.objects.create(
                organization=org, created_by=requester_user,
                patient_reference="DEMO-PT-001", required_by=now + timedelta(days=3),
                urgency="ROUTINE", clinical_indication="Demo: scheduled surgery",
                status="SUBMITTED",
            )
            RequestItem.objects.create(request=req, blood_type=bt["A+"], component=prbc, quantity=2)
        # 2) emergency SUBMITTED
        if not BloodRequest.objects.filter(urgency="EMERGENCY").exists():
            req = BloodRequest.objects.create(
                organization=org, created_by=requester_user,
                patient_reference="DEMO-PT-002", required_by=now + timedelta(hours=12),
                urgency="EMERGENCY", clinical_indication="Demo: acute blood loss",
                status="SUBMITTED",
            )
            RequestItem.objects.create(request=req, blood_type=bt["O-"], component=wb, quantity=3)
        # 3) approved with one allocation against Ana Santos' released O+ bag
        if not BloodRequest.objects.filter(status__in=["APPROVED", "PARTIALLY_FULFILLED", "FULFILLED"]).exists():
            req = BloodRequest.objects.create(
                organization=org, created_by=requester_user,
                patient_reference="DEMO-PT-003", required_by=now + timedelta(days=1),
                urgency="URGENT", clinical_indication="Demo: transfusion support",
                status="APPROVED", approved_by=admin, approved_at=now,
                assigned_staff=staff,
            )
            item = RequestItem.objects.create(request=req, blood_type=bt["O+"], component=wb, quantity=2)
            from requests.services import BloodRequestService
            candidate = None
            for bag in released_bags:
                bag.refresh_from_db()
                if (bag.status == "AVAILABLE" and str(bag.blood_type) == "O+"
                        and bag.component_id == wb.pk and not bag.is_expired_by_date):
                    candidate = bag
                    break
            if candidate:
                BloodRequestService.allocate_bag(item, candidate, actor=staff)
                self.stdout.write(f"Reserved {candidate.bag_code} for {req.request_code}.")

        self.stdout.write(f"Blood requests present: {BloodRequest.objects.count()}")

        # --- one pending self-registration for the review queue -------------
        from accounts.models import RegistrationRequest

        if not RegistrationRequest.objects.filter(username="pending.donor").exists():
            pending_user = User.objects.create_user(
                username="pending.donor", password=self.password,
                email="pending.donor@example.test", first_name="Nena", last_name="Nakasalin",
                role="DONOR", phone="09170000009", is_active=False)
            RegistrationRequest.objects.create(
                first_name="Nena", last_name="Nakasalin", username="pending.donor",
                email="pending.donor@example.test", phone="09170000009", role="DONOR",
                date_of_birth=today - timedelta(days=365 * 26),
                address="Demo Street, Zamboanga City", municipality="Zamboanga City",
                province="Zamboanga del Sur", user=pending_user)
            self.stdout.write("Created pending demo registration 'pending.donor' "
                              "(inactive user — approve/reject under Registrations).")
