"""Core: styled form classes, template components, and CSRF enforcement."""
from django import forms
from django.test import Client, TestCase
from django.urls import reverse

from core.forms import StyledFormMixin
from core.testing import make_user


class StyledFormTests(TestCase):
    def test_mixins_apply_tailwind_classes(self):
        class DemoForm(StyledFormMixin, forms.Form):
            name = forms.CharField()

        html = str(DemoForm()["name"])
        self.assertIn("border", html)  # styling classes applied server-side

    def test_bound_errors_available(self):
        class DemoForm(StyledFormMixin, forms.Form):
            name = forms.CharField(required=True)

        f = DemoForm(data={})
        f.full_clean()
        self.assertIn("name", f.errors)


class TemplateComponentTests(TestCase):
    def test_pagination_component_renders(self):
        # Regression guard: brace-bearing {% %} tags inside {# #} comments used to
        # compile as live nodes and made components/pagination.html include itself.
        # Render it through a real paginated page instead of an isolated Template.
        for i in range(25):
            make_user(f"core-pg-{i}", role="ADMIN")
        make_user("core-pg-admin", role="ADMIN")
        self.client.login(username="core-pg-admin", password="Test12345!")
        resp = self.client.get(reverse("accounts:user_list") + "?page=2")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("Pagination", content)
        self.assertIn("page=1", content)  # previous-page link preserves the query string

    def test_component_templates_have_no_tag_braces_in_comments(self):
        import pathlib
        import re
        bad = []
        root = pathlib.Path(__file__).resolve().parent.parent / "templates"
        for path in root.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"\{#(.*?)#\}", text, flags=re.S):
                if "{" in m.group(1) or "%" in m.group(1):
                    bad.append((str(path), "braces/% in comment (compiles as live code)"))
                elif "\n" in m.group(1):
                    bad.append((str(path), "multi-line comment (Django renders it as visible text)"))
        self.assertEqual(bad, [], f"leaking template comments: {bad}")


class CsrfTests(TestCase):
    def test_login_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/accounts/login/").status_code, 200)  # page reachable
        resp = client.post("/accounts/login/", {"username": "x", "password": "y"})
        self.assertEqual(resp.status_code, 403)


class PublicPathsTests(TestCase):
    """Regression: LOGIN_URL is a URL *name*; the middleware must compare paths."""

    def test_login_page_visible_to_anonymous(self):
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 200)

    def test_password_reset_pages_visible_to_anonymous(self):
        self.assertEqual(self.client.get("/accounts/password-reset/").status_code, 200)
