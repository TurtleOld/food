from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ShellTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_authenticated_page_has_navigation_with_member_and_logout(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("core:day"))

        for label in ("Дневник", "Продукты", "Цель", "Выйти"):
            self.assertContains(response, label)
        self.assertContains(response, reverse("core:product_list"))
        self.assertContains(response, reverse("core:daily_target_edit"))
        self.assertContains(response, "alice")

    def test_active_nav_item_is_marked_current(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("core:product_list"))

        self.assertContains(response, 'aria-current="page"', count=2)  # top bar + tab bar

    def test_every_diary_and_catalog_page_marks_its_section(self):
        self.client.force_login(self.member)
        pages = {
            "diary": reverse("core:entry_create", args=["2026-01-05"]),
            "diary ": reverse("core:day_on", args=["2026-01-05"]),
            "products": reverse("core:product_create"),
            "target": reverse("core:daily_target_edit"),
        }
        for section, url in pages.items():
            with self.subTest(section=section):
                response = self.client.get(url)
                self.assertContains(response, 'aria-current="page"', count=2)

    def test_login_page_has_no_navigation(self):
        response = self.client.get(reverse("core:login"))

        self.assertNotContains(response, "Выйти")
        self.assertNotContains(response, reverse("core:product_list"))

    def test_body_carries_csrf_header_for_htmx(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("core:day"))

        pattern = r"""<body[^>]*hx-headers='\{"X-CSRFToken": "[^"]+"\}'"""
        self.assertRegex(response.content.decode(), pattern)

    def test_htmx_history_cache_is_disabled_and_boost_unused(self):
        self.client.force_login(self.member)

        html = self.client.get(reverse("core:day")).content.decode()

        self.assertIn("htmx.config.historyCacheSize = 0", html)
        self.assertNotIn("hx-boost", html)

    def test_page_uses_only_vendored_assets(self):
        self.client.force_login(self.member)

        html = self.client.get(reverse("core:day")).content.decode()

        self.assertNotIn("https://", html)
        self.assertIn("vendor/bulma/bulma-1.0.4.min.css", html)
        self.assertIn("vendor/htmx/htmx-2.0.11.min.js", html)
        self.assertIn("vendor/alpine/alpine-3.17.4.min.js", html)
        for path in (
            "vendor/bulma/bulma-1.0.4.min.css",
            "vendor/htmx/htmx-2.0.11.min.js",
            "vendor/alpine/alpine-3.17.4.min.js",
        ):
            self.assertIsNotNone(finders.find(path))

    def test_dark_theme_follows_system(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, '<meta name="color-scheme" content="light dark">')
        css_path = finders.find("css/app.css")
        assert isinstance(css_path, str)
        with open(css_path) as css:
            self.assertIn("prefers-color-scheme: dark", css.read())

    def test_messages_after_redirect_render_in_toast_container(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("core:daily_target_edit"),
            {"calories": 2000, "proteins": 100, "fats": 70, "carbs": 250},
            follow=True,
        )

        self.assertContains(response, 'id="toasts"')
        self.assertContains(response, "Цель сохранена")
