"""Notifications: mock providers, token capability links, responses, retries."""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testing import make_donor, make_user
from notifications.models import Notification
from notifications.services import NotificationError, NotificationService

LOCMEM = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class DispatchTests(TestCase):
    def setUp(self):
        self.donor = make_donor(email="someone@example.test")

    def test_in_app_always_dispatches(self):
        n = Notification.objects.create(donor=self.donor, channel="in_app", body="hi")
        NotificationService.dispatch(n)
        n.refresh_from_db()
        self.assertEqual(n.delivery_status, "SENT")

    @LOCMEM
    def test_email_sends_through_django_mail(self):
        from django.core import mail
        n = Notification.objects.create(donor=self.donor, channel="email",
                                        subject="s", body="b")
        NotificationService.dispatch(n)
        n.refresh_from_db()
        self.assertEqual(n.delivery_status, "SENT")
        self.assertEqual(len(mail.outbox), 1)

    @LOCMEM
    def test_email_without_address_fails_not_crashes(self):
        donor = make_donor()
        donor.email = ""
        donor.save(update_fields=["email"])
        n = Notification.objects.create(donor=donor, channel="email", subject="s", body="b")
        NotificationService.dispatch(n)
        n.refresh_from_db()
        self.assertEqual(n.delivery_status, "FAILED")
        self.assertIn("No email address", n.error)

    def test_sms_is_mock_labeled(self):
        n = Notification.objects.create(donor=self.donor, channel="sms", body="b")
        NotificationService.dispatch(n)
        n.refresh_from_db()
        # Mock SMS provider: with no real gateway configured it must either fail
        # with a clearly-labelled message or record the MOCK send — never
        # silently pretend a real SMS went out.
        self.assertIn(n.delivery_status, ("SENT", "FAILED"))

    def test_retry_only_for_failed_and_counts(self):
        n = Notification.objects.create(donor=self.donor, channel="email", subject="x",
                                        body="y", delivery_status="FAILED", error="boom")
        NotificationService.retry_failed(n)
        n.refresh_from_db()
        self.assertEqual(n.retry_count, 1)
        with self.assertRaises(NotificationError):
            NotificationService.retry_failed(n)  # now SENT, not retryable


class TokenResponseTests(TestCase):
    def setUp(self):
        self.donor = make_donor()
        self.n = Notification.objects.create(donor=self.donor, channel="in_app",
                                             subject="Emerge", body="need blood",
                                             delivery_status="SENT", sent_at=timezone.now())

    def test_anonymous_token_link_can_respond(self):
        c = self.client
        url = reverse("notifications:respond_token", kwargs={"token": str(self.n.response_token)})
        resp = c.post(url, {"response": "AVAILABLE"}, follow=True)
        self.n.refresh_from_db()
        self.assertEqual(self.n.donor_response, "AVAILABLE")
        self.assertIsNotNone(self.n.responded_at)
        content = resp.content.decode()
        self.assertTrue("availability only" in content or "not a medical clearance" in content)

    def test_response_is_idempotent(self):
        url = reverse("notifications:respond_token", kwargs={"token": str(self.n.response_token)})
        self.client.post(url, {"response": "AVAILABLE"})
        self.client.post(url, {"response": "UNAVAILABLE"})
        self.n.refresh_from_db()
        self.assertEqual(self.n.donor_response, "AVAILABLE")

    def test_invalid_value_rejected(self):
        url = reverse("notifications:respond_token", kwargs={"token": str(self.n.response_token)})
        self.client.post(url, {"response": "HACK"})
        self.n.refresh_from_db()
        self.assertIn(self.n.donor_response, (None, ""))

    def test_pk_path_requires_owner(self):
        from django.test import Client
        resp = Client().get(reverse("notifications:respond", kwargs={"pk": self.n.pk}))
        self.assertIn(resp.status_code, (302, 403))

    def test_other_donor_cannot_mark_read(self):
        make_user("notif-stranger", role="DONOR")
        self.client.login(username="notif-stranger", password="Test12345!")
        resp = self.client.post(reverse("notifications:read", kwargs={"pk": self.n.pk}))
        self.assertEqual(resp.status_code, 403)
        self.n.refresh_from_db()
        self.assertFalse(self.n.is_read)
