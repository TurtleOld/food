from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class DayPageTest(TestCase):
    """Personal day page: the app's entry point behind authentication."""

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("core:day"))

        self.assertRedirects(response, f"{reverse('core:login')}?next={reverse('core:day')}")

    def test_member_sees_personal_empty_day(self):
        member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(member)

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "alice")
        self.assertContains(response, "Записей пока нет")

    def test_health_endpoint_is_available(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
