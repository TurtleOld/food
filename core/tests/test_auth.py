from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class LoginTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_login_page_is_public(self):
        response = self.client.get(reverse("core:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вход")

    def test_member_logs_in_and_reaches_diary(self):
        response = self.client.post(
            reverse("core:login"),
            {"username": "alice", "password": "s3cret-pass"},
            follow=True,
        )

        self.assertContains(response, "alice")
        self.assertContains(response, "Записей пока нет")

    def test_login_returns_to_requested_page(self):
        response = self.client.post(
            f"{reverse('core:login')}?next={reverse('core:day')}",
            {
                "username": "alice",
                "password": "s3cret-pass",
                "next": reverse("core:day"),
            },
        )

        self.assertRedirects(response, reverse("core:day"))

    def test_wrong_password_does_not_log_in(self):
        response = self.client.post(
            reverse("core:login"),
            {"username": "alice", "password": "not-the-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(
            self.client.get(reverse("core:day")),
            f"{reverse('core:login')}?next={reverse('core:day')}",
        )

    def test_login_page_shows_brand_and_form_fields(self):
        response = self.client.get(reverse("core:login"))

        self.assertContains(response, "КБЖУ")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password" ')
        self.assertContains(response, 'autocomplete="current-password"')

    def test_wrong_password_shows_error_in_form(self):
        response = self.client.post(
            reverse("core:login"),
            {"username": "alice", "password": "not-the-password"},
        )

        self.assertContains(response, "Неверное имя пользователя или пароль")
        self.assertContains(response, 'value="alice"')

    def test_login_page_keeps_next_for_the_form(self):
        target = reverse("core:product_list")

        response = self.client.get(f"{reverse('core:login')}?next={target}")

        self.assertContains(response, f'name="next" value="{target}"')

    def test_failed_login_keeps_next(self):
        target = reverse("core:product_list")

        response = self.client.post(
            reverse("core:login"),
            {"username": "alice", "password": "bad", "next": target},
        )

        self.assertContains(response, f'name="next" value="{target}"')

    def test_next_survives_client_redirect_to_login(self):
        """Целевой адрес из HX-Redirect доходит до входа и возвращает назад."""
        target = reverse("core:product_list")
        login_url = f"{reverse('core:login')}?next={target}"

        page = self.client.get(login_url)
        response = self.client.post(
            login_url,
            {"username": "alice", "password": "s3cret-pass", "next": target},
        )

        self.assertContains(page, f'name="next" value="{target}"')
        self.assertRedirects(response, target)

    def test_logout_ends_session(self):
        self.client.force_login(self.member)

        response = self.client.post(reverse("core:logout"))

        self.assertRedirects(response, reverse("core:login"))
        self.assertRedirects(
            self.client.get(reverse("core:day")),
            f"{reverse('core:login')}?next={reverse('core:day')}",
        )

    def test_public_registration_is_not_available(self):
        for path in ("/signup/", "/register/", "/accounts/signup/", "/accounts/register/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class DiaryPrivacyTest(TestCase):
    """Day pages belong to the signed-in account and are never shared."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="alice-pass")
        self.bob = User.objects.create_user(username="bob", password="bob-pass")

    def test_members_see_only_their_own_diary(self):
        alice = Client()
        alice.force_login(self.alice)
        bob = Client()
        bob.force_login(self.bob)

        alice_page = alice.get(reverse("core:day")).content.decode()
        bob_page = bob.get(reverse("core:day")).content.decode()

        self.assertIn("alice", alice_page)
        self.assertNotIn("bob", alice_page)
        self.assertIn("bob", bob_page)
        self.assertNotIn("alice", bob_page)

    def test_another_members_diary_has_no_route(self):
        self.client.force_login(self.alice)

        guessed_path = f"/members/{self.bob.pk}/diary/"

        self.assertEqual(self.client.get(guessed_path).status_code, 404)

    def test_session_survives_application_restart(self):
        """Sessions are stored server-side, so a fresh process reuses them.

        The test cannot restart a process, so it proves the property that makes
        restart survival possible: the session lives in the database and a new
        client holding the same cookie is authenticated.
        """
        self.client.post(
            reverse("core:login"),
            {"username": "alice", "password": "alice-pass"},
        )
        session_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value

        restarted = Client()
        restarted.cookies[settings.SESSION_COOKIE_NAME] = session_key
        response = restarted.get(reverse("core:day"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice")
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())
