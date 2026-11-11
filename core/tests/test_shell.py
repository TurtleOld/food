import re

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

    def test_profile_chip_carries_member_name_to_profile(self):
        self.client.force_login(self.member)

        html = self.client.get(reverse("core:day")).content.decode()

        profile_links = re.findall(
            rf'<a[^>]*href="{re.escape(reverse("core:profile"))}"[^>]*>.*?</a>',
            html,
            re.S,
        )
        carried = [link for link in profile_links if "alice" in link]
        self.assertEqual(len(carried), 1)
        self.assertIn('aria-label="Профиль: alice"', carried[0])
        self.assertIn("<svg", carried[0])

    def test_logout_is_a_post_form_with_csrf(self):
        self.client.force_login(self.member)

        html = self.client.get(reverse("core:day")).content.decode()

        form = re.search(
            rf'<form[^>]*action="{re.escape(reverse("core:logout"))}"[^>]*>.*?</form>',
            html,
            re.S,
        )
        self.assertIsNotNone(form)
        assert form is not None
        self.assertIn('method="post"', form.group(0))
        self.assertIn("csrfmiddlewaretoken", form.group(0))
        self.assertNotRegex(html, rf'<a[^>]*href="{re.escape(reverse("core:logout"))}"')

    def test_topbar_and_tabbar_list_the_five_sections_in_the_same_order(self):
        self.client.force_login(self.member)

        html = self.client.get(reverse("core:day")).content.decode()

        expected = [
            reverse("core:day"),
            reverse("core:product_list"),
            reverse("core:daily_target_edit"),
            reverse("assistant:chat"),
            reverse("core:profile"),
        ]

        def sections(markup):
            return [href for href in re.findall(r'href="([^"]+)"', markup) if href in expected]

        topbar = re.search(r'<header class="topbar">.*?</header>', html, re.S)
        tabbar = re.search(r'<nav class="tabbar".*?</nav>', html, re.S)
        assert topbar is not None and tabbar is not None

        self.assertEqual(sections(topbar.group(0)), expected)
        self.assertEqual(sections(tabbar.group(0)), expected)

    def test_tabbar_labels_stay_readable_on_a_phone(self):
        css_path = finders.find("css/app.css")
        assert isinstance(css_path, str)
        with open(css_path) as css:
            rules = re.findall(r"\.tabbar a \{(.*?)\}", css.read(), re.S)

        sizes = [
            float(size) for rule in rules for size in re.findall(r"font-size:\s*([\d.]+)rem", rule)
        ]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 0.8)

    def test_toast_style_is_defined_once(self):
        css_path = finders.find("css/app.css")
        assert isinstance(css_path, str)
        with open(css_path) as css:
            selectors = re.findall(r"^\.toast\s*\{", css.read(), re.M)

        self.assertEqual(len(selectors), 1)

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
