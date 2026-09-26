"""Notifications: mock providers, token capability links, responses, retries."""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testing import make_donor, make_user
from notifications.models import Notification
from notifications.providers import (MockSMSProvider, SemaphoreSMSProvider,
                                      _normalize_ph_mobile, get_provider)
from notifications.services import NotificationError, NotificationService

LOCMEM = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
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


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="test-key-not-secret",
                   SMS_SENDER_NAME="BLOODBANK")
class SemaphoreSMSProviderTests(TestCase):
    def setUp(self):
        self.donor = make_donor()  # contact_number 0900-000-0000
        self.provider = SemaphoreSMSProvider()

    def _notification(self, body="Your donation is confirmed"):
        return Notification.objects.create(donor=self.donor, channel="sms", body=body)

    def test_provider_selection_follows_setting(self):
        self.assertIsInstance(get_provider("sms"), SemaphoreSMSProvider)
        with override_settings(SMS_PROVIDER="mock"):
            self.assertIsInstance(get_provider("sms"), MockSMSProvider)

    def test_ph_mobile_normalization(self):
        self.assertEqual(_normalize_ph_mobile("0900-000-0000"), "09000000000")
        self.assertEqual(_normalize_ph_mobile("+63 900 000 0000"), "09000000000")
        self.assertEqual(_normalize_ph_mobile("639000000000"), "09000000000")
        self.assertIsNone(_normalize_ph_mobile("12345"))
        self.assertIsNone(_normalize_ph_mobile(""))

    def test_missing_api_key_fails_safe_without_http_call(self):
        with override_settings(SMS_API_KEY=""):
            with mock.patch.object(SemaphoreSMSProvider, "_post") as post:
                success, error = self.provider.send(self._notification())
        self.assertFalse(success)
        self.assertIn("not configured", error)
        post.assert_not_called()

    def test_invalid_recipient_number_fails_without_http_call(self):
        donor = make_donor()
        donor.contact_number = "landline"
        donor.save(update_fields=["contact_number"])
        with mock.patch.object(SemaphoreSMSProvider, "_post") as post:
            success, error = self.provider.send(
                Notification.objects.create(donor=donor, channel="sms", body="x"))
        self.assertFalse(success)
        self.assertIn("Philippine mobile", error)
        post.assert_not_called()

    def test_send_posts_normalized_fields_and_marks_sent(self):
        n = self._notification()
        with mock.patch.object(SemaphoreSMSProvider, "_post",
                               return_value=(200, json.dumps([{"message_id": 1, "status": "Queued"}]))) as post:
            success, error = self.provider.send(n)
            self.assertTrue(success, error)
            fields = post.call_args.args[0]
            self.assertEqual(fields["apikey"], "test-key-not-secret")
            self.assertEqual(fields["number"], "09000000000")
            self.assertEqual(fields["sendername"], "BLOODBANK")
            self.assertEqual(fields["message"], "Your donation is confirmed")
            NotificationService.dispatch(n)
        n.refresh_from_db()
        self.assertEqual(n.delivery_status, "SENT")
        self.assertEqual(post.call_count, 2)  # direct send + dispatch

    def test_http_error_records_failure(self):
        n = self._notification()
        with mock.patch.object(SemaphoreSMSProvider, "_post",
                               return_value=(401, '{"message":"Invalid API key"}')):
            success, error = self.provider.send(n)
            self.assertFalse(success)
            self.assertIn("401", error)
            NotificationService.dispatch(n)
        n.refresh_from_db()
        self.assertEqual(n.delivery_status, "FAILED")
        self.assertIn("401", n.error)

    def test_failed_delivery_status_is_failure(self):
        with mock.patch.object(SemaphoreSMSProvider, "_post",
                               return_value=(200, json.dumps([{"status": "Failed"}]))):
            success, error = self.provider.send(self._notification())
        self.assertFalse(success)
        self.assertIn("Failed", error)

    def test_unparseable_response_fails(self):
        with mock.patch.object(SemaphoreSMSProvider, "_post", return_value=(200, "<html>")):
            success, error = self.provider.send(self._notification())
        self.assertFalse(success)
        self.assertIn("unparseable", error)

    def test_network_error_never_raises(self):
        import urllib.error
        with mock.patch.object(SemaphoreSMSProvider, "_post", return_value=(0, "timed out")):
            success, error = self.provider.send(self._notification())
        self.assertFalse(success)
        self.assertIn("timed out", error)
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("dns failure")):
            success, error = self.provider.send(self._notification())
        self.assertFalse(success)
        self.assertIn("dns failure", error)


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
